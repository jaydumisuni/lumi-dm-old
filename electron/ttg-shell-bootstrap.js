"use strict";

const electron = require("electron");
const { app, ipcMain } = electron;
const NativeBrowserWindow = electron.BrowserWindow;

// Apply the THETECHGUY shell to full application windows before main.js creates
// them. Small widgets and confirmation popups already own their custom surfaces.
class TTGBrowserWindow extends NativeBrowserWindow {
  constructor(options = {}) {
    const isMainSurface = Number(options.width || 0) >= 650 && !options.parent;
    super(isMainSurface ? {
      ...options,
      frame: false,
      titleBarStyle: "hidden",
      autoHideMenuBar: true,
      roundedCorners: true,
      backgroundColor: options.backgroundColor || "#070a11",
    } : options);
  }
}

Object.setPrototypeOf(TTGBrowserWindow, NativeBrowserWindow);
try {
  Object.defineProperty(electron, "BrowserWindow", {
    value: TTGBrowserWindow,
    configurable: true,
    writable: true,
  });
} catch (_) {
  electron.BrowserWindow = TTGBrowserWindow;
}

function ownerWindow(event) {
  return NativeBrowserWindow.fromWebContents(event.sender);
}

ipcMain.handle("ttg-window-control", (event, action) => {
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

ipcMain.handle("ttg-window-state", event => {
  const window = ownerWindow(event);
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
  website: "https://thetechguyds.com",
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
