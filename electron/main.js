"use strict";

const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  Notification,
  dialog,
  ipcMain,
  nativeImage,
  screen,
  shell,
} = require("electron");
const fs = require("fs");
const http = require("http");
const path = require("path");

app.setName("Lumi DM");
if (process.platform === "win32") app.setAppUserModelId("com.lumi.dm");

if (app.isPackaged) process.env.LUMIDM_BRANDING_DIR = path.join(process.resourcesPath, "Resouces");
else process.env.LUMIDM_BRANDING_DIR = path.resolve(__dirname, "..", "Resouces");

require("./native-session");
const serverSupervisor = require("./server-supervisor");
require("./connection-capacity");
require("./desktop-command-poller");
const { UpdateManager } = require("./update-manager");

const API_HOST = "127.0.0.1";
const API_PORT = 7000;
const API_ORIGIN = `http://${API_HOST}:${API_PORT}`;
const LOGIN_ARGS = ["--hidden", "--login-startup"];
const LEGACY_LOGIN_ARGS = ["--hidden"];
const ACTIVE_STATES = new Set(["queued", "resolving", "running", "pausing", "post_processing"]);

let mainWindow = null;
let widgetWindow = null;
let setupWindow = null;
let tray = null;
let updater = null;
let isQuitting = false;
let widgetExpanded = false;
let setupResolved = false;
let pollingTimer = null;
let setupTimer = null;
let baselineReady = false;
let lastSpeed = 0;
let lastActive = 0;
const taskBaseline = new Map();
const setupData = new Map();
const shownHandoffs = new Set();

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) app.quit();

function isHiddenLaunch(argv = process.argv) {
  return argv.includes("--hidden") || argv.includes("--login-startup");
}

function isStartupLaunch(argv = process.argv) {
  if (process.platform === "win32") return isHiddenLaunch(argv);
  const settings = app.getLoginItemSettings();
  return settings.wasOpenedAtLogin || settings.wasOpenedAsHidden || isHiddenLaunch(argv);
}

function getLoginOptions(args = LOGIN_ARGS) { return { path: process.execPath, args }; }

function getStartupEnabled() {
  if (process.platform === "linux") return readGeneralPrefs().startAtLogin === true;
  if (process.platform === "win32") {
    const current = app.getLoginItemSettings(getLoginOptions());
    if (current.openAtLogin) return current.enabled !== false;
    const legacy = app.getLoginItemSettings(getLoginOptions(LEGACY_LOGIN_ARGS));
    return legacy.openAtLogin && legacy.enabled !== false;
  }
  return app.getLoginItemSettings().openAtLogin;
}

function setStartupEnabled(enabled) {
  if (process.platform === "linux") writeGeneralPrefs({ ...readGeneralPrefs(), startAtLogin: enabled });
  else if (process.platform === "win32") {
    app.setLoginItemSettings({ ...getLoginOptions(LEGACY_LOGIN_ARGS), openAtLogin: false });
    app.setLoginItemSettings({ ...getLoginOptions(), openAtLogin: enabled });
  } else app.setLoginItemSettings({ openAtLogin: enabled, openAsHidden: true, args: ["--hidden"] });
  rebuildTrayMenu();
}

function iconPath() {
  if (process.platform === "win32") {
    return app.isPackaged
      ? path.join(process.resourcesPath, "assets", "windows", "Lumi-DM.ico")
      : path.resolve(__dirname, "..", "assets", "windows", "Lumi-DM.ico");
  }
  return app.isPackaged
    ? path.join(process.resourcesPath, "static", "favicon-256.png")
    : path.resolve(__dirname, "..", "static", "favicon-256.png");
}

function generalPrefsPath() { return path.join(app.getPath("userData"), "LUMIDM-prefs.json"); }
function desktopPrefsPath() { return path.join(app.getPath("userData"), "LUMIDM-desktop.json"); }
function readJson(file, fallback) {
  try { return { ...fallback, ...JSON.parse(fs.readFileSync(file, "utf8")) }; }
  catch (_) { return { ...fallback }; }
}
function writeJson(file, value) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), "utf8");
    fs.renameSync(temporary, file);
  } catch (_) {}
}
function readGeneralPrefs() { return readJson(generalPrefsPath(), {}); }
function writeGeneralPrefs(value) { writeJson(generalPrefsPath(), value); }
function defaultDesktopPrefs() {
  return { corner: "bottom-right", displayId: "primary", margin: 12, scale: 1, visible: true, showUpload: false };
}
function readDesktopPrefs() { return readJson(desktopPrefsPath(), defaultDesktopPrefs()); }
function writeDesktopPrefs(value) {
  const next = { ...readDesktopPrefs(), ...(value || {}) };
  writeJson(desktopPrefsPath(), next);
  return next;
}

function displayFor(settings) {
  if (String(settings.displayId) === "primary") return screen.getPrimaryDisplay();
  return screen.getAllDisplays().find(display => String(display.id) === String(settings.displayId)) || screen.getPrimaryDisplay();
}
function displaysForUi() {
  const primary = screen.getPrimaryDisplay();
  return screen.getAllDisplays().map((display, index) => ({
    id: String(display.id),
    label: `${display.id === primary.id ? "Primary" : `Display ${index + 1}`} · ${display.workArea.width}×${display.workArea.height}`,
  }));
}
function cornerBounds(width, height, settings = readDesktopPrefs()) {
  const area = displayFor(settings).workArea;
  const margin = Math.max(4, Math.min(80, Number(settings.margin || 12)));
  const left = String(settings.corner).endsWith("left");
  const top = String(settings.corner).startsWith("top");
  return {
    x: Math.round(left ? area.x + margin : area.x + area.width - width - margin),
    y: Math.round(top ? area.y + margin : area.y + area.height - height - margin),
    width,
    height,
  };
}

function requestJson(method, route, body = null, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const payload = body === null ? null : Buffer.from(JSON.stringify(body));
    const request = http.request({
      hostname: API_HOST,
      port: API_PORT,
      path: route,
      method,
      timeout,
      headers: {
        "X-Lumi-Client": "electron-desktop",
        ...(payload ? { "Content-Type": "application/json", "Content-Length": payload.length } : {}),
      },
    }, response => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", chunk => { raw += chunk; });
      response.on("end", () => {
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; }
        catch (_) { data = { error: raw.slice(0, 300) }; }
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(data.error || `Lumi API ${response.statusCode}`));
          return;
        }
        resolve(data);
      });
    });
    request.on("timeout", () => request.destroy(new Error("Lumi server timed out")));
    request.on("error", reject);
    if (payload) request.write(payload);
    request.end();
  });
}

async function waitForServer(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await serverSupervisor.checkReady(1800)) return true;
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  return false;
}

function createMainWindow(startHidden = false) {
  if (mainWindow && !mainWindow.isDestroyed()) return mainWindow;
  mainWindow = new BrowserWindow({
    width: 920,
    height: 650,
    minWidth: 720,
    minHeight: 500,
    center: true,
    show: false,
    frame: false,
    title: "Lumi DM",
    icon: iconPath(),
    autoHideMenuBar: true,
    backgroundColor: "#070a11",
    webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload-main.js") },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("close", event => {
    if (isQuitting) return;
    event.preventDefault();
    mainWindow.hide();
    showWidget();
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  mainWindow.on("maximize", broadcastWindowState);
  mainWindow.on("unmaximize", broadcastWindowState);
  mainWindow.on("focus", broadcastWindowState);
  mainWindow.on("blur", broadcastWindowState);

  const staticIndex = app.isPackaged
    ? path.join(process.resourcesPath, "static", "index.html")
    : path.resolve(__dirname, "..", "static", "index.html");
  void (async () => {
    const ready = await waitForServer();
    if (mainWindow?.isDestroyed()) return;
    if (ready) await mainWindow.loadURL(API_ORIGIN);
    else await mainWindow.loadFile(staticIndex);
    if (!startHidden) mainWindow.show();
  })();
  return mainWindow;
}
function showMainWindow() {
  const window = createMainWindow(false);
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
function broadcastWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send("ttg-window-state-changed", {
    maximized: mainWindow.isMaximized(),
    focused: mainWindow.isFocused(),
  });
}

function applyWidgetBounds() {
  if (!widgetWindow || widgetWindow.isDestroyed()) return;
  const settings = readDesktopPrefs();
  const scale = Math.max(0.75, Math.min(1.35, Number(settings.scale || 1)));
  widgetWindow.setBounds(cornerBounds(
    Math.round((widgetExpanded ? 360 : 240) * scale),
    Math.round((widgetExpanded ? 320 : 66) * scale),
    settings,
  ), true);
}
function createWidget() {
  const settings = readDesktopPrefs();
  if (widgetWindow && !widgetWindow.isDestroyed()) return widgetWindow;
  const scale = Math.max(0.75, Math.min(1.35, Number(settings.scale || 1)));
  widgetWindow = new BrowserWindow({
    ...cornerBounds(Math.round(240 * scale), Math.round(66 * scale), settings),
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
    webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload-widget.js") },
  });
  widgetWindow.setAlwaysOnTop(true, "floating");
  widgetWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: false });
  void widgetWindow.loadFile(path.join(__dirname, "widget.html"));
  widgetWindow.on("closed", () => { widgetWindow = null; });
  widgetWindow.once("ready-to-show", () => {
    if (settings.visible !== false && !setupWindow) widgetWindow.showInactive();
  });
  return widgetWindow;
}
function showWidget() {
  const settings = readDesktopPrefs();
  if (settings.visible === false || setupWindow) return;
  createWidget();
  applyWidgetBounds();
  widgetWindow.showInactive();
}
function hideWidget() {
  if (widgetWindow && !widgetWindow.isDestroyed()) widgetWindow.hide();
}
async function widgetSnapshot() {
  try {
    const [downloads, net] = await Promise.all([
      requestJson("GET", "/api/downloads?limit=100"),
      requestJson("GET", "/api/netstats").catch(() => ({})),
    ]);
    return { online: true, downloads: downloads.downloads || [], net, settings: readDesktopPrefs(), expanded: widgetExpanded };
  } catch (error) {
    return { online: false, error: String(error.message || error), downloads: [], net: {}, settings: readDesktopPrefs(), expanded: widgetExpanded };
  }
}

async function setupOptions(task) {
  const [settings, queues, categories] = await Promise.all([
    requestJson("GET", "/api/settings").catch(() => ({})),
    requestJson("GET", "/api/queues").catch(() => ({ queues: [] })),
    requestJson("GET", "/api/categories").catch(() => ({ categories: [] })),
  ]);
  return { task, settings, queues: queues.queues || [], categories: categories.categories || [] };
}
async function showSetupPopup(task) {
  if (setupWindow && !setupWindow.isDestroyed()) return;
  const handoffId = String(task.metadata?.browser_handoff_id || "");
  if (!handoffId || shownHandoffs.has(handoffId)) return;
  shownHandoffs.add(handoffId);
  setupResolved = false;
  hideWidget();
  const scale = Math.max(0.85, Math.min(1.2, Number(readDesktopPrefs().scale || 1)));
  setupWindow = new BrowserWindow({
    ...cornerBounds(Math.round(450 * scale), Math.round(485 * scale), readDesktopPrefs()),
    frame: false,
    transparent: true,
    hasShadow: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: true,
    show: false,
    webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload-confirm.js") },
  });
  setupWindow.setAlwaysOnTop(true, "floating");
  const webContentsId = setupWindow.webContents.id;
  setupData.set(webContentsId, { handoffId, ...(await setupOptions(task)) });
  void setupWindow.loadFile(path.join(__dirname, "confirm.html"));
  setupWindow.once("ready-to-show", () => { setupWindow?.show(); setupWindow?.focus(); });
  setupWindow.on("closed", () => {
    const data = setupData.get(webContentsId);
    setupData.delete(webContentsId);
    setupWindow = null;
    if (!setupResolved && data?.handoffId) {
      void requestJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/browser`, {}).catch(() => {});
    }
    showWidget();
  });
}
async function scanPendingSetups() {
  if (setupWindow) return;
  try {
    const result = await requestJson("GET", "/api/downloads?limit=200");
    const pending = (result.downloads || []).find(task => task.status === "browser_pending" && task.metadata?.browser_handoff_id);
    if (pending) await showSetupPopup(pending);
  } catch (_) {}
}
function closeSetup(resolved = true) {
  setupResolved = resolved;
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
}

function formatSpeed(value) {
  const bytes = Number(value || 0);
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB/s`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB/s`;
  return `${bytes.toFixed(0)} B/s`;
}
function notifyCompletion(task) {
  if (!Notification.isSupported()) return;
  const notification = new Notification({
    title: "Download complete",
    body: task.filename || "File downloaded",
    icon: iconPath(),
    silent: false,
  });
  notification.on("click", () => {
    void requestJson("POST", `/api/downloads/${encodeURIComponent(task.id)}/open`, {}).catch(() => {});
    showMainWindow();
  });
  notification.show();
}
async function pollTasks() {
  try {
    const result = await requestJson("GET", "/api/downloads?limit=200", null, 5000);
    const downloads = result.downloads || [];
    lastActive = downloads.filter(task => task.status === "running").length;
    lastSpeed = downloads.reduce((sum, task) => sum + Number(task.speed_bytes_per_sec || 0), 0);
    if (tray) tray.setToolTip(lastActive ? `Lumi DM · ↓ ${formatSpeed(lastSpeed)} · ${lastActive} active` : "Lumi DM");
    if (!baselineReady) {
      for (const task of downloads) taskBaseline.set(String(task.id), String(task.status || ""));
      baselineReady = true;
    } else {
      const liveIds = new Set();
      for (const task of downloads) {
        const id = String(task.id);
        const status = String(task.status || "");
        const previous = taskBaseline.get(id);
        liveIds.add(id);
        if (status === "completed" && previous && ACTIVE_STATES.has(previous)) notifyCompletion(task);
        taskBaseline.set(id, status);
      }
      for (const id of taskBaseline.keys()) if (!liveIds.has(id)) taskBaseline.delete(id);
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      const running = downloads.filter(task => task.status === "running");
      if (running.length) {
        const average = running.reduce((sum, task) => sum + Number(task.progress_percent || 0), 0) / running.length;
        mainWindow.setProgressBar(average / 100);
      } else mainWindow.setProgressBar(-1);
    }
  } catch (_) {}
}

function rebuildTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Lumi DM", enabled: false },
    { type: "separator" },
    { label: "Open Lumi Manager", click: showMainWindow },
    { label: "Show connection widget", click: showWidget },
    { type: "separator" },
    {
      label: "Run at Windows startup",
      type: "checkbox",
      checked: getStartupEnabled(),
      click: item => setStartupEnabled(item.checked),
    },
    { type: "separator" },
    { label: "Quit", click: () => { isQuitting = true; app.quit(); } },
  ]));
}
function createTray() {
  const image = nativeImage.createFromPath(iconPath());
  tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
  tray.setToolTip("Lumi DM");
  tray.on("click", showWidget);
  tray.on("double-click", showMainWindow);
  rebuildTrayMenu();
}

function registerIpc() {
  ipcMain.handle("pick-folder", async event => {
    const owner = BrowserWindow.fromWebContents(event.sender) || mainWindow || BrowserWindow.getFocusedWindow();
    const result = await dialog.showOpenDialog(owner, {
      title: "Choose download folder",
      properties: ["openDirectory", "createDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle("ttg-window-control", (event, action) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window || window.isDestroyed()) return { ok: false, maximized: false };
    if (action === "minimize") window.minimize();
    if (action === "maximize") window.isMaximized() ? window.unmaximize() : window.maximize();
    if (action === "close") window.close();
    return { ok: true, maximized: window.isMaximized() };
  });
  ipcMain.handle("ttg-window-state", event => {
    const window = BrowserWindow.fromWebContents(event.sender);
    return {
      maximized: Boolean(window && !window.isDestroyed() && window.isMaximized()),
      focused: Boolean(window && !window.isDestroyed() && window.isFocused()),
    };
  });
  ipcMain.handle("ttg-app-info", () => ({
    name: app.getName(),
    version: app.getVersion(),
    platform: process.platform,
    architecture: process.arch,
    publisher: "THETECHGUY DIGITAL SOLUTIONS",
    website: "https://thetechguyds.com/tools",
  }));

  ipcMain.handle("v5-desktop-settings-get", () => ({ ...readDesktopPrefs(), displays: displaysForUi() }));
  ipcMain.handle("v5-desktop-settings-save", (_event, value) => {
    const next = writeDesktopPrefs(value);
    widgetExpanded = false;
    if (next.visible === false) hideWidget(); else showWidget();
    widgetWindow?.webContents.send("v5-settings-changed", next);
    return { ...next, displays: displaysForUi() };
  });
  ipcMain.on("v5-widget-show", showWidget);
  ipcMain.on("v5-widget-show-main", showMainWindow);
  ipcMain.handle("v5-widget-snapshot", widgetSnapshot);
  ipcMain.handle("v5-widget-toggle", () => {
    widgetExpanded = !widgetExpanded;
    if (widgetWindow && !widgetWindow.isDestroyed()) {
      widgetWindow.setFocusable(widgetExpanded);
      applyWidgetBounds();
      widgetWindow.webContents.send("v5-expanded", widgetExpanded);
      widgetExpanded ? widgetWindow.show() : widgetWindow.showInactive();
    }
    return widgetExpanded;
  });
  ipcMain.handle("v5-widget-action", async (_event, action, taskId = "") => {
    if (action === "pause-all") return requestJson("POST", "/api/downloads/pause-all", {});
    if (action === "resume-all") return requestJson("POST", "/api/downloads/resume-all", {});
    if (action === "pause" && taskId) return requestJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/pause`, {});
    if (action === "resume" && taskId) return requestJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/resume`, {});
    if (action === "cancel" && taskId) return requestJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/cancel`, {});
    if (action === "open" && taskId) return requestJson("POST", `/api/downloads/${encodeURIComponent(taskId)}/open`, {});
    if (action === "main") { showMainWindow(); return { ok: true }; }
    return { ok: false };
  });
  ipcMain.handle("v5-setup-data", event => setupData.get(event.sender.id) || null);
  ipcMain.handle("v5-setup-pick-folder", async event => {
    const owner = BrowserWindow.fromWebContents(event.sender) || setupWindow;
    const result = await dialog.showOpenDialog(owner, {
      title: "Choose download folder",
      properties: ["openDirectory", "createDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle("v5-setup-confirm", async (event, value) => {
    const data = setupData.get(event.sender.id);
    if (!data?.handoffId) throw new Error("Setup handoff unavailable");
    const result = await requestJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/confirm`, value || {});
    closeSetup(true);
    return result;
  });
  ipcMain.handle("v5-setup-browser", async event => {
    const data = setupData.get(event.sender.id);
    if (!data?.handoffId) throw new Error("Setup handoff unavailable");
    const result = await requestJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/browser`, {});
    closeSetup(true);
    return result;
  });
  ipcMain.handle("v5-setup-cancel", async event => {
    const data = setupData.get(event.sender.id);
    if (!data?.handoffId) throw new Error("Setup handoff unavailable");
    const result = await requestJson("POST", `/api/v5/browser/handoffs/${encodeURIComponent(data.handoffId)}/cancel`, {});
    closeSetup(true);
    return result;
  });
}

app.on("second-instance", (_event, argv) => { if (!isHiddenLaunch(argv)) showMainWindow(); });
app.whenReady().then(() => {
  if (!gotSingleInstanceLock) return;
  Menu.setApplicationMenu(null);
  registerIpc();
  createTray();
  serverSupervisor.start();
  createMainWindow(isStartupLaunch());
  createWidget();
  showWidget();
  updater = new UpdateManager({
    onStatus: status => {
      for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send("v5-update-status", status);
      }
    },
  });
  ipcMain.handle("v5-update-check", (_event, manual) => updater.check(Boolean(manual)));
  void updater.check(false);
  pollingTimer = setInterval(() => void pollTasks(), 1800);
  setupTimer = setInterval(() => void scanPendingSetups(), 700);
  void pollTasks();
});
app.on("before-quit", () => {
  isQuitting = true;
  if (pollingTimer) clearInterval(pollingTimer);
  if (setupTimer) clearInterval(setupTimer);
  if (widgetWindow && !widgetWindow.isDestroyed()) widgetWindow.destroy();
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.destroy();
  serverSupervisor.stop();
});
app.on("window-all-closed", () => {});
app.on("activate", showMainWindow);
