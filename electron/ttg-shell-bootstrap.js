"use strict";

const Module = require("module");
const electron = require("electron");
const { app, BrowserWindow: NativeBrowserWindow, ipcMain } = electron;

// Apply the TTG frame only to Lumi's legacy main.js constructor. Modern
// Electron exposes BrowserWindow as a read-only getter, so mutating the export
// crashes packaged applications. A parent-scoped module proxy is safe: main.js
// receives a compatible constructor while Electron itself remains untouched.
class TTGBrowserWindow extends NativeBrowserWindow {
  constructor(options = {}) {
    const isMainSurface = Number(options.width || 0) >= 650 && !options.parent;
    super(isMainSurface ? {
      ...options,
      frame: false,
      title: "Lumi DM",
      titleBarStyle: "hidden",
      autoHideMenuBar: true,
      roundedCorners: true,
      backgroundColor: options.backgroundColor || "#070a11",
    } : options);
    if (isMainSurface) this.setMenuBarVisibility(false);
  }
}
Object.setPrototypeOf(TTGBrowserWindow, NativeBrowserWindow);

const nativeLoad = Module._load;
Module._load = function ttgElectronProxy(request, parent, isMain) {
  const loaded = nativeLoad.apply(this, arguments);
  const parentFile = String(parent?.filename || "").replace(/\\/g, "/");
  if (request === "electron" && /\/electron\/main\.js$/i.test(parentFile)) {
    return { ...loaded, BrowserWindow: TTGBrowserWindow };
  }
  return loaded;
};

function ownerWindow(event) {
  return NativeBrowserWindow.fromWebContents(event.sender);
}

function registerHandle(channel, handler) {
  try { ipcMain.removeHandler(channel); } catch (_) {}
  ipcMain.handle(channel, handler);
}

registerHandle("ttg-window-control", (event, action) => {
  const window = ownerWindow(event);
  if (!window || window.isDestroyed()) return { ok: false, maximized: false };
  if (action === "minimize") window.minimize();
  if (action === "maximize") {
    if (window.isMaximized()) window.unmaximize();
    else window.maximize();
  }
  if (action === "close") window.close();
  return { ok: true, maximized: window.isMaximized() };
});

registerHandle("ttg-window-state", event => {
  const window = ownerWindow(event);
  return {
    maximized: Boolean(window && !window.isDestroyed() && window.isMaximized()),
    focused: Boolean(window && !window.isDestroyed() && window.isFocused()),
  };
});

registerHandle("ttg-app-info", () => ({
  name: app.getName(),
  version: app.getVersion(),
  platform: process.platform,
  architecture: process.arch,
  publisher: "THETECHGUY DIGITAL SOLUTIONS",
  website: "https://thetechguyds.com/tools",
}));

app.on("browser-window-created", (_event, window) => {
  if (window.getBounds().width < 650) return;
  const notify = () => {
    if (!window.isDestroyed()) {
      window.webContents.send("ttg-window-state-changed", {
        maximized: window.isMaximized(),
        focused: window.isFocused(),
      });
    }
  };
  window.on("maximize", notify);
  window.on("unmaximize", notify);
  window.on("focus", notify);
  window.on("blur", notify);
});
