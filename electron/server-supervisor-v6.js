"use strict";

/* Recover the local Lumi server when Windows login starts Electron before the
 * packaged Python process is ready, and reconnect windows that loaded fallback
 * content during a slow boot. */
const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

let ownedProcess = null;
let quitting = false;
let consecutiveFailures = 0;
let restartAttempts = 0;
let lastRestartAt = 0;
let wasReady = false;
let timer = null;

function serverCommand() {
  const env = { ...process.env };
  if (app.isPackaged) {
    const extension = process.platform === "win32" ? ".exe" : "";
    env.LUMIDM_STATIC_DIR = path.join(process.resourcesPath, "static");
    env.LUMIDM_DATA_DIR = app.getPath("userData");
    return {
      command: path.join(process.resourcesPath, "server", `LUMIDM-server${extension}`),
      args: ["--host", "127.0.0.1", "--port", "7000"],
      env,
    };
  }
  return {
    command: process.env.LUMIDM_PYTHON || (process.platform === "win32" ? "python" : "python3"),
    args: [path.resolve(__dirname, "..", "server.py"), "--host", "127.0.0.1", "--port", "7000"],
    env,
  };
}

function checkReady(timeout = 2500) {
  return new Promise(resolve => {
    const request = http.get({ hostname: "127.0.0.1", port: 7000, path: "/api/downloads?limit=1", timeout }, response => {
      response.resume();
      resolve((response.statusCode || 500) < 500);
    });
    request.on("timeout", () => { request.destroy(); resolve(false); });
    request.on("error", () => resolve(false));
  });
}

function spawnServer() {
  if (quitting || (ownedProcess && !ownedProcess.killed)) return;
  const now = Date.now();
  if (now - lastRestartAt < 2500) return;
  lastRestartAt = now;
  restartAttempts += 1;
  const spec = serverCommand();
  try {
    ownedProcess = spawn(spec.command, spec.args, { stdio: "ignore", env: spec.env, windowsHide: true });
    ownedProcess.once("error", () => { ownedProcess = null; });
    ownedProcess.once("exit", () => { ownedProcess = null; });
  } catch (_) { ownedProcess = null; }
}

function reconnectFallbackWindows() {
  for (const window of BrowserWindow.getAllWindows()) {
    if (window.isDestroyed()) continue;
    const bounds = window.getBounds();
    if (bounds.width < 650) continue;
    const url = window.webContents.getURL();
    if (!url || url.startsWith("file:") || url.startsWith("chrome-error:") || url === "about:blank") {
      void window.loadURL("http://127.0.0.1:7000");
    }
    window.webContents.send("lumi-server-state", { ready: true, recovered: true });
  }
}

async function tick() {
  const ready = await checkReady();
  if (ready) {
    consecutiveFailures = 0;
    restartAttempts = 0;
    if (!wasReady) reconnectFallbackWindows();
    wasReady = true;
    return;
  }

  consecutiveFailures += 1;
  wasReady = false;
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send("lumi-server-state", { ready: false, failures: consecutiveFailures });
  }
  if (consecutiveFailures >= 3 && restartAttempts < 6) spawnServer();
  if (restartAttempts >= 6 && Date.now() - lastRestartAt > 60_000) restartAttempts = 0;
}

app.whenReady().then(() => {
  setTimeout(() => {
    void tick();
    timer = setInterval(() => void tick(), 2500);
  }, 2200);
});

app.on("before-quit", () => {
  quitting = true;
  if (timer) clearInterval(timer);
  if (ownedProcess && !ownedProcess.killed) {
    try {
      if (process.platform === "win32") {
        require("child_process").spawnSync("taskkill", ["/PID", String(ownedProcess.pid), "/F", "/T"]);
      } else ownedProcess.kill("SIGTERM");
    } catch (_) {}
  }
});

module.exports = { checkReady, tick };
