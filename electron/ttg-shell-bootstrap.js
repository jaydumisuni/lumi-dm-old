"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");

// Window framing is configured directly in main.js. Do not monkey-patch
// Electron's exported BrowserWindow property: modern Electron exposes it as a
// read-only getter and assigning to it crashes the packaged main process.
function ownerWindow(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

function registerHandle(channel, handler) {
  try {
    ipcMain.removeHandler(channel);
  } catch (_) {}
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
