"use strict";

/**
 * Disables only obsolete shell behaviours that conflict with Lumi V5.
 *
 * The proven legacy Electron shell still owns tray integration, startup handling,
 * notifications and server lifecycle. V5 owns the permanent widget and download
 * setup popup, so the old widget and clipboard-to-full-window timer are suppressed.
 */
const electron = require("electron");
const { app, BrowserWindow } = electron;

const originalSetInterval = global.setInterval;
global.setInterval = function lumiV5Interval(callback, delay, ...args) {
  if (typeof callback === "function" && callback.name === "checkClipboard") {
    return {
      ref() { return this; },
      unref() { return this; },
      hasRef() { return false; },
      refresh() { return this; },
      [Symbol.toPrimitive]() { return 0; },
    };
  }
  return originalSetInterval(callback, delay, ...args);
};

app.on("browser-window-created", (_event, window) => {
  setTimeout(() => {
    if (window.isDestroyed()) return;
    const bounds = window.getBounds();
    if (bounds.width === 220 && bounds.height === 60) {
      window.destroy();
    }
  }, 0);
});

// Never let the legacy staged-task watcher raise the full manager. The new V5
// browser handoff uses its own `browser_pending` state and corner setup window.
for (const method of ["show", "focus"]) {
  const original = BrowserWindow.prototype[method];
  BrowserWindow.prototype[method] = function guardedLegacyStagedWindow(...args) {
    const stack = String(new Error().stack || "");
    if (stack.includes("showMainWindowForStaged")) return;
    return original.apply(this, args);
  };
}
