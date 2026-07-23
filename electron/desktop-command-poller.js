"use strict";

const { app, BrowserWindow } = require("electron");
const http = require("http");

function request(method, route, body = null) {
  return new Promise((resolve, reject) => {
    const payload = body === null ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({
      hostname: "127.0.0.1", port: 7000, path: route, method, timeout: 2200,
      headers: {
        "X-Lumi-Client": "electron-command-v5",
        ...(payload ? { "Content-Type": "application/json", "Content-Length": payload.length } : {}),
      },
    }, response => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", chunk => raw += chunk);
      response.on("end", () => {
        try { resolve(raw ? JSON.parse(raw) : {}); }
        catch { resolve({}); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function mainWindow() {
  return BrowserWindow.getAllWindows().find(window => {
    const bounds = window.getBounds();
    return !window.isDestroyed() && bounds.width >= 650 && bounds.height >= 430;
  });
}

function widgetWindow() {
  return BrowserWindow.getAllWindows().find(window => {
    const bounds = window.getBounds();
    return !window.isDestroyed() && window.isAlwaysOnTop() && bounds.width <= 500 && bounds.height <= 360;
  });
}

async function poll() {
  try {
    const result = await request("GET", "/api/v5/desktop/command");
    const command = result.command;
    if (!command?.id) return;
    if (command.action === "show-main") {
      const window = mainWindow();
      if (window) {
        if (window.isMinimized()) window.restore();
        window.show();
        window.focus();
      }
    }
    if (command.action === "show-widget") widgetWindow()?.showInactive();
    await request("POST", `/api/v5/desktop/command/${encodeURIComponent(command.id)}/ack`, {});
  } catch (_) {}
}

app.whenReady().then(() => setTimeout(() => setInterval(poll, 650), 1800));
