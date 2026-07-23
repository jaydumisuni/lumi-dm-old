"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");

const DOWNLOAD_ENDPOINT = "https://speed.cloudflare.com/__down";
const UPLOAD_ENDPOINT = "https://speed.cloudflare.com/__up";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
let runningPromise = null;
let currentStatus = {
  state: "idle",
  result: null,
  message: "Connection capacity has not been tested yet.",
};

function resultPath() {
  return path.join(app.getPath("userData"), "LUMIDM-connection-capacity.json");
}

function loadResult() {
  try {
    const result = JSON.parse(fs.readFileSync(resultPath(), "utf8"));
    return result && typeof result === "object" ? result : null;
  } catch (_) { return null; }
}

function saveResult(result) {
  try {
    fs.mkdirSync(path.dirname(resultPath()), { recursive: true });
    const temporary = `${resultPath()}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(result, null, 2), "utf8");
    fs.renameSync(temporary, resultPath());
  } catch (_) {}
}

function broadcast() {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send("v6-capacity-status", currentStatus);
  }
}

function requestJson(route) {
  return new Promise((resolve, reject) => {
    const request = http.get({
      hostname: "127.0.0.1",
      port: 7000,
      path: route,
      timeout: 5000,
    }, response => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", chunk => { raw += chunk; });
      response.on("end", () => {
        try { resolve(raw ? JSON.parse(raw) : {}); }
        catch (_) { reject(new Error("Lumi returned invalid connection-test state")); }
      });
    });
    request.on("timeout", () => request.destroy(new Error("Lumi server timed out")));
    request.on("error", reject);
  });
}

async function assertIdle() {
  try {
    const response = await requestJson("/api/downloads?limit=200");
    const active = (response.downloads || []).filter(task => ["running", "resolving", "pausing"].includes(task.status));
    if (active.length) throw new Error("Pause active downloads before testing connection capacity.");
  } catch (error) {
    if (/Pause active/.test(String(error.message || error))) throw error;
  }
}

function timedRequest(url, { method = "GET", body = null, expectedBytes = 0, timeout = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    const started = process.hrtime.bigint();
    let received = 0;
    const parsed = new URL(url);
    const request = https.request({
      method,
      hostname: parsed.hostname,
      path: `${parsed.pathname}${parsed.search}`,
      headers: {
        "User-Agent": "Lumi-DM-Connection-Test/1.0",
        "Cache-Control": "no-store",
        ...(body ? { "Content-Type": "application/octet-stream", "Content-Length": body.length } : {}),
      },
      timeout,
    }, response => {
      response.on("data", chunk => { received += chunk.length; });
      response.on("end", () => {
        const elapsed = Number(process.hrtime.bigint() - started) / 1e9;
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(`Capacity endpoint returned ${response.statusCode}`));
          return;
        }
        const bytes = method === "POST" ? Number(body?.length || 0) : received;
        if (expectedBytes && bytes < expectedBytes * 0.8) {
          reject(new Error("Capacity sample ended before enough data arrived"));
          return;
        }
        resolve({ seconds: Math.max(0.001, elapsed), bytes });
      });
    });
    request.on("timeout", () => request.destroy(new Error("Capacity sample timed out")));
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return 0;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

async function latencySamples(count = 5) {
  const values = [];
  for (let index = 0; index < count; index += 1) {
    const sample = await timedRequest(`${DOWNLOAD_ENDPOINT}?bytes=0&cache=${Date.now()}-${index}`, { timeout: 8000 });
    values.push(sample.seconds * 1000);
  }
  return median(values);
}

async function downloadSamples() {
  const values = [];
  for (const bytes of [1_000_000, 5_000_000, 15_000_000]) {
    const sample = await timedRequest(`${DOWNLOAD_ENDPOINT}?bytes=${bytes}&cache=${Date.now()}`, {
      expectedBytes: bytes,
      timeout: 30000,
    });
    values.push(sample.bytes * 8 / sample.seconds / 1_000_000);
  }
  return median(values.slice(-2));
}

async function uploadSamples() {
  const values = [];
  for (const bytes of [500_000, 2_000_000, 5_000_000]) {
    const body = Buffer.alloc(bytes, 0x4c);
    const sample = await timedRequest(`${UPLOAD_ENDPOINT}?cache=${Date.now()}`, {
      method: "POST",
      body,
      timeout: 30000,
    });
    values.push(sample.bytes * 8 / sample.seconds / 1_000_000);
  }
  return median(values.slice(-2));
}

async function runCapacityTest() {
  if (runningPromise) return runningPromise;
  runningPromise = (async () => {
    await assertIdle();
    currentStatus = {
      state: "running",
      result: currentStatus.result || loadResult(),
      message: "Testing download capacity…",
    };
    broadcast();
    const latencyMs = await latencySamples();
    const downloadMbps = await downloadSamples();
    currentStatus = {
      state: "running",
      result: currentStatus.result,
      message: "Testing upload capacity…",
    };
    broadcast();
    const uploadMbps = await uploadSamples();
    const result = {
      download_mbps: Number(downloadMbps.toFixed(2)),
      upload_mbps: Number(uploadMbps.toFixed(2)),
      latency_ms: Number(latencyMs.toFixed(1)),
      provider: "Cloudflare edge",
      tested_at: new Date().toISOString(),
    };
    saveResult(result);
    currentStatus = {
      state: "complete",
      result,
      message: "Connection capacity test completed.",
    };
    broadcast();
    return currentStatus;
  })().catch(error => {
    currentStatus = {
      state: "error",
      result: loadResult(),
      message: String(error.message || error),
    };
    broadcast();
    return currentStatus;
  }).finally(() => { runningPromise = null; });
  return runningPromise;
}

const stored = loadResult();
if (stored) {
  const age = Date.now() - Date.parse(stored.tested_at || 0);
  currentStatus = {
    state: "complete",
    result: stored,
    message: age > MAX_AGE_MS ? "Capacity result is older than seven days." : "Last connection capacity result.",
  };
}

ipcMain.handle("v6-capacity-status", () => currentStatus);
ipcMain.handle("v6-capacity-run", () => runCapacityTest());

module.exports = { runCapacityTest, getStatus: () => currentStatus };
