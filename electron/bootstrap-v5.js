"use strict";

const electron = require("electron");
const { app, BrowserWindow, ipcMain, screen, shell } = electron;
const fs = require("fs");
const path = require("path");
const http = require("http");
const { UpdateManager } = require("./update-manager");

if (app.isPackaged) {
  process.env.LUMIDM_BRANDING_DIR = path.join(process.resourcesPath, "Resouces");
} else {
  process.env.LUMIDM_BRANDING_DIR = path.resolve(__dirname, "..", "Resouces");
}

let persistentWidget = null;
let setupWindow = null;
let mainWindowV5 = null;
let widgetExpanded = false;
let setupResolved = false;
let updater = null;
const setupData = new Map();
const shownHandoffs = new Set();

function prefsPath() { return path.join(app.getPath("userData"), "LUMIDM-desktop-v5.json"); }
function defaultPrefs() {
  return { corner: "bottom-right", displayId: "primary", margin: 12, scale: 1, visible: true, showUpload: false };
}
function loadPrefs() {
  try { return { ...defaultPrefs(), ...JSON.parse(fs.readFileSync(prefsPath(), "utf8")) }; }
  catch { return defaultPrefs(); }
}
function savePrefs(value) {
  const next = { ...loadPrefs(), ...value };
  try {
    fs.mkdirSync(path.dirname(prefsPath()), { recursive: true });
    const temporary = `${prefsPath()}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(next, null, 2), "utf8");
    fs.renameSync(temporary, prefsPath());
  } catch (_) {}
  return next;
}

function displayFor(settings) {
  const displays = screen.getAllDisplays();
  if (String(settings.displayId) === "primary") return screen.getPrimaryDisplay();
  return displays.find(display => String(display.id) === String(settings.displayId)) || screen.getPrimaryDisplay();
}

function displaysForUi() {
  const primary = screen.getPrimaryDisplay();
  return screen.getAllDisplays().map((display, index) => ({
    id: String(display.id),
    label: `${display.id === primary.id ? "Primary" : `Display ${index + 1}`} · ${display.workArea.width}×${display.workArea.height}`,
  }));
}

function boundsFor(width, height, settings = loadPrefs()) {
  const display = displayFor(settings);
  const area = display.workArea;
  const margin = Math.max(4, Math.min(80, Number(settings.margin || 12)));
  const left = String(settings.corner).endsWith("left");
  const top = String(settings.corner).startsWith("top");
  return {
    x: Math.round(left ? area.x + margin : area.x + area.width - width - margin),
    y: Math.round(top ? area.y + margin : area.y + area.height - height - margin),
    width, height,
  };
}

function applyWidgetBounds() {
  if (!persistentWidget || persistentWidget.isDestroyed()) return;
  const settings = loadPrefs();
  const scale = Math.max(.75, Math.min(1.35, Number(settings.scale || 1)));
  const width = Math.round((widgetExpanded ? 360 : 240) * scale);
  const height = Math.round((widgetExpanded ? 320 : 66) * scale);
  persistentWidget.setBounds(boundsFor(width, height, settings), true);
}

function createPersistentWidget() {
  const settings = loadPrefs();
  if (persistentWidget && !persistentWidget.isDestroyed()) {
    applyWidgetBounds();
    if (settings.visible !== false && !setupWindow) persistentWidget.showInactive();
    return persistentWidget;
  }
  const scale = Math.max(.75, Math.min(1.35, Number(settings.scale || 1)));
  persistentWidget = new BrowserWindow({
    ...boundsFor(Math.round(240 * scale), Math.round(66 * scale), settings),
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    show: false,
    alwaysOnTop: true,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "preload-widget-v5.js"),
    },
  });
  persistentWidget.setAlwaysOnTop(true, "floating");
  persistentWidget.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: false });
  persistentWidget.loadFile(path.join(__dirname, "widget-v5.html"));
  persistentWidget.on("closed", () => { persistentWidget = null; });
  persistentWidget.once("ready-to-show", () => {
    if (settings.visible !== false && !setupWindow) persistentWidget.showInactive();
  });
  return persistentWidget;
}

function showPersistentWidget() {
  const settings = loadPrefs();
  if (settings.visible === false || setupWindow) return;
  createPersistentWidget();
  applyWidgetBounds();
  persistentWidget.showInactive();
}

function hidePersistentWidget() {
  if (persistentWidget && !persistentWidget.isDestroyed()) persistentWidget.hide();
}

function showMainWindow() {
  if (!mainWindowV5 || mainWindowV5.isDestroyed()) {
    mainWindowV5 = BrowserWindow.getAllWindows().find(win => win !== persistentWidget && win !== setupWindow && win.getBounds().width >= 650) || null;
  }
  if (mainWindowV5) {
    if (mainWindowV5.isMinimized()) mainWindowV5.restore();
    mainWindowV5.show();
    mainWindowV5.focus();
  }
}

function httpJson(method, route, body = null) {
  return new Promise((resolve, reject) => {
    const payload = body === null ? null : Buffer.from(JSON.stringify(body));
    const request = http.request({
      hostname: "127.0.0.1", port: 7000, path: route, method,
      headers: {
        "X-Lumi-Client": "electron-desktop-v5",
        ...(payload ? { "Content-Type": "application/json", "Content-Length": payload.length } : {}),
      },
      timeout: 8000,
    }, response => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", chunk => raw += chunk);
      response.on("end", () => {
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch { data = { error: raw.slice(0, 300) }; }
        if ((response.statusCode || 500) >= 400) return reject(new Error(data.error || `Lumi API ${response.statusCode}`));
        resolve(data);
      });
    });
    request.on("timeout", () => request.destroy(new Error("Lumi server timed out")));
    request.on("error", reject);
    if (payload) request.write(payload);
    request.end();
  });
}

async function widgetSnapshot() {
  try {
    const [downloads, net] = await Promise.all([
      httpJson("GET", "/api/downloads?limit=100"),
      httpJson("GET", "/api/netstats").catch(() => ({})),
    ]);
    return { online: true, downloads: downloads.downloads || [], net, settings: loadPrefs(), expanded: widgetExpanded };
  } catch (error) {
    return { online: false, error: error.message, downloads: [], net: {}, settings: loadPrefs(), expanded: widgetExpanded };
  }
}

function setupPosition(width, height) { return boundsFor(width, height, loadPrefs()); }

async function setupOptions(task) {
  const [settings, queues, categories] = await Promise.all([
    httpJson("GET", "/api/settings").catch(() => ({})),
    httpJson("GET", "/api/queues").catch(() => ({ queues: [] })),
    httpJson("GET", "/api/categories").catch(() => ({ categories: [] })),
  ]);
  return { task, settings, queues: queues.queues || [], categories: categories.categories || [] };
}

async function showSetupPopup(task) {
  if (setupWindow && !setupWindow.isDestroyed()) return;
  const handoffId = String(task.metadata?.browser_handoff_id || "");
  if (!handoffId || shownHandoffs.has(handoffId)) return;
  shownHandoffs.add(handoffId);
  setupResolved = false;
  hidePersistentWidget();
  const scale = Math.max(.85, Math.min(1.2, Number(loadPrefs().scale || 1)));
  const width = Math.round(450 * scale), height = Math.round(485 * scale);
  setupWindow = new BrowserWindow({
    ...setupPosition(width, height),
    frame: false,
    transparent: true,
    hasShadow: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: true,
    show: false,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "preload-confirm-v5.js"),
    },
  });
  setupWindow.setAlwaysOnTop(true, "floating");
  setupData.set(setupWindow.webContents.id, { handoffId, ...(await setupOptions(task)) });
  setupWindow.loadFile(path.join(__dirname, "confirm-v5.html"));
  setupWindow.once("ready-to-show", () => { setupWindow.show(); setupWindow.focus(); });
  setupWindow.on("closed", () => {
    const data = setupData.get(setupWindow?.webContents?.id);
    setupData.delete(setupWindow?.webContents?.id);
    const pendingId = data?.handoffId || handoffId;
    setupWindow = null;
    if (!setupResolved && pendingId) {
      void httpJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(pendingId)}/browser`, {}).catch(() => {});
    }
    showPersistentWidget();
  });
}

async function scanPendingSetups() {
  if (setupWindow) return;
  try {
    const result = await httpJson("GET", "/api/downloads?limit=200");
    const pending = (result.downloads || []).find(task => task.status === "browser_pending" && task.metadata?.browser_handoff_id);
    if (pending) await showSetupPopup(pending);
  } catch (_) {}
}

function closeSetup(resolved = true) {
  setupResolved = resolved;
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
}

app.on("browser-window-created", (_event, window) => {
  setTimeout(() => {
    if (window.isDestroyed()) return;
    const bounds = window.getBounds();
    if (window !== persistentWidget && bounds.width === 220 && bounds.height === 60 && persistentWidget) {
      window.close();
      showPersistentWidget();
      return;
    }
    if (window !== persistentWidget && window !== setupWindow && bounds.width >= 650) {
      mainWindowV5 = window;
      window.setTitle("Lumi Download Manager");
      window.webContents.on("did-finish-load", () => window.setTitle("Lumi Download Manager"));
    }
  }, 40);
});

ipcMain.handle("v5-desktop-settings-get", () => ({ ...loadPrefs(), displays: displaysForUi() }));
ipcMain.handle("v5-desktop-settings-save", (_event, value) => {
  const next = savePrefs(value || {});
  widgetExpanded = false;
  if (next.visible === false) hidePersistentWidget(); else showPersistentWidget();
  if (persistentWidget && !persistentWidget.isDestroyed()) persistentWidget.webContents.send("v5-settings-changed", next);
  return { ...next, displays: displaysForUi() };
});
ipcMain.on("v5-widget-show", () => showPersistentWidget());
ipcMain.on("v5-widget-show-main", () => showMainWindow());
ipcMain.handle("v5-widget-snapshot", () => widgetSnapshot());
ipcMain.handle("v5-widget-toggle", () => {
  widgetExpanded = !widgetExpanded;
  if (persistentWidget && !persistentWidget.isDestroyed()) {
    persistentWidget.setFocusable(widgetExpanded);
    applyWidgetBounds();
    persistentWidget.webContents.send("v5-expanded", widgetExpanded);
    if (widgetExpanded) persistentWidget.show(); else persistentWidget.showInactive();
  }
  return widgetExpanded;
});
ipcMain.handle("v5-widget-action", async (_event, action, taskId = "") => {
  if (action === "pause-all") return httpJson("POST", "/api/downloads/pause-all", {});
  if (action === "resume-all") return httpJson("POST", "/api/downloads/resume-all", {});
  if (action === "pause" && taskId) return httpJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/pause`, {});
  if (action === "resume" && taskId) return httpJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/resume`, {});
  if (action === "cancel" && taskId) return httpJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/cancel`, {});
  if (action === "open" && taskId) return httpJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/open`, {});
  if (action === "main") { showMainWindow(); return { ok: true }; }
  return { ok: false };
});
ipcMain.handle("v5-setup-data", event => setupData.get(event.sender.id) || null);
ipcMain.handle("v5-setup-pick-folder", async event => {
  const owner = BrowserWindow.fromWebContents(event.sender) || setupWindow;
  const result = await electron.dialog.showOpenDialog(owner, { title: "Choose download folder", properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle("v5-setup-confirm", async (event, value) => {
  const data = setupData.get(event.sender.id);
  if (!data?.handoffId) throw new Error("Setup handoff unavailable");
  const result = await httpJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/confirm`, value || {});
  closeSetup(true);
  return result;
});
ipcMain.handle("v5-setup-browser", async event => {
  const data = setupData.get(event.sender.id);
  if (!data?.handoffId) throw new Error("Setup handoff unavailable");
  const result = await httpJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/browser`, {});
  closeSetup(true);
  return result;
});
ipcMain.handle("v5-setup-cancel", async event => {
  const data = setupData.get(event.sender.id);
  if (!data?.handoffId) throw new Error("Setup handoff unavailable");
  const result = await httpJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/cancel`, {});
  closeSetup(true);
  return result;
});

app.whenReady().then(() => {
  updater = new UpdateManager({
    onStatus: status => {
      for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send("v5-update-status", status);
      }
    },
  });
  ipcMain.handle("v5-update-check", (_event, manual) => updater.check(Boolean(manual)));
  setTimeout(() => {
    createPersistentWidget();
    showPersistentWidget();
    void updater.check(false);
    setInterval(scanPendingSetups, 700);
  }, 1300);
});

app.on("before-quit", () => {
  if (persistentWidget && !persistentWidget.isDestroyed()) persistentWidget.destroy();
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.destroy();
});

require("./main.js");
