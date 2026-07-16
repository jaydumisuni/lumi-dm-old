/* Lumi DM — popup logic */
"use strict";

let _selectedType = "auto";
let _server = "http://localhost:7000";

function _fetch(url, opts = {}, timeout = 5000) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), timeout);
  return fetch(url, { ...opts, signal: ctrl.signal })
    .finally(() => clearTimeout(tid));
}

let _listLoading = false;

document.addEventListener("DOMContentLoaded", async () => {
  // Load saved server
  const { server, interceptEnabled } = await chrome.storage.local.get({
    server: "http://localhost:7000",
    interceptEnabled: true,
  });
  _server = server;
  const si = document.getElementById("server-input");
  if (si) si.value = _server;

  // Intercept toggle
  const toggle = document.getElementById("intercept-toggle");
  if (toggle) {
    toggle.checked = interceptEnabled;
    toggle.addEventListener("change", () => {
      chrome.runtime.sendMessage({ type: "SET_INTERCEPT", enabled: toggle.checked });
    });
  }

  // Auto-fill current tab URL into input
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url && !tab.url.startsWith("chrome")) {
    const urlEl = document.getElementById("url-input");
    if (urlEl) urlEl.value = tab.url;
  }

  // Type buttons
  document.querySelectorAll(".type-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".type-btn").forEach(b => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      _selectedType = btn.dataset.type;
    });
  });

  // Go button
  document.getElementById("go-btn")?.addEventListener("click", sendDownload);
  document.getElementById("url-input")?.addEventListener("keydown", e => {
    if (e.key === "Enter") sendDownload();
  });

  // Open full UI
  document.getElementById("open-ui")?.addEventListener("click", () => {
    chrome.tabs.create({ url: _server });
  });

  // Clear done
  document.getElementById("clear-btn")?.addEventListener("click", async () => {
    await _fetch(`${_server}/api/downloads/clear`, { method: "POST" }).catch(() => {});
    loadList();
  });

  // Save server
  document.getElementById("save-server")?.addEventListener("click", () => {
    const val = document.getElementById("server-input")?.value.trim();
    if (val) {
      _server = val.replace(/\/$/, "");
      chrome.storage.local.set({ server: _server });
      loadList();
    }
  });

  // From all open tabs
  document.getElementById("all-tabs-btn")?.addEventListener("click", queueAllTabs);

  loadList();
  setInterval(loadList, 3000);
});

async function sendDownload() {
  const urlEl = document.getElementById("url-input");
  const url   = (urlEl?.value || "").trim();
  if (!url) return;

  let type = _selectedType;
  if (type === "auto") {
    if (url.startsWith("magnet:") || url.endsWith(".torrent")) type = "torrent";
    else type = "http";
  }

  chrome.runtime.sendMessage(
    { type: "DOWNLOAD", url, dlType: type },
    res => {
      if (res?.ok) {
        setMsg("Download started!");
        if (urlEl) urlEl.value = "";
        setTimeout(loadList, 800);
      } else {
        setMsg(`Failed: ${res?.result?.error || "Unknown error"}`);
      }
    }
  );
}

async function loadList() {
  if (_listLoading) return;
  _listLoading = true;
  try {
    const [dlRes, netRes] = await Promise.all([
      _fetch(`${_server}/api/downloads?limit=20`).then(r => r.json()),
      _fetch(`${_server}/api/netstats`).then(r => r.json()).catch(() => ({ rx_bps: 0 })),
    ]);
    const jobs    = Array.isArray(dlRes.downloads) ? dlRes.downloads : [];
    const inetBps = netRes.capacity_bps || netRes.rx_bps || 0;
    _updateSpeedBar(jobs, inetBps);
    const list = document.getElementById("dl-list");
    if (!list) return;
    if (!jobs.length) {
      list.innerHTML = `<div class="empty-msg">No downloads yet.</div>`;
      return;
    }
    list.innerHTML = jobs.map(j => {
      const pct  = Math.min(100, Number(j.progress_percent || 0)).toFixed(0);
      const name = j.filename || j.id;
      let label;
      if (j.status === "running") {
        const speed = +j.speed_bytes_per_sec || 0;
        const remaining = Math.max(0, (+j.total_bytes || 0) - (+j.downloaded_bytes || 0));
        const eta = speed > 0 && remaining > 0 ? fmtEta(remaining / speed) : "";
        label = eta || `${pct}%`;
      } else {
        label = j.status === "completed" ? "✓" : `${pct}%`;
      }
      return `
        <div class="dl-item">
          <span class="dl-dot ${j.status}"></span>
          <span class="dl-name" title="${esc(name)}">${esc(name)}</span>
          <span class="dl-pct">${esc(label)}</span>
        </div>`;
    }).join("");
  } catch (_) {
    const list = document.getElementById("dl-list");
    if (list) list.innerHTML = `<div class="empty-msg">Cannot reach server at ${esc(_server)}</div>`;
  } finally {
    _listLoading = false;
  }
}

// Two-step: first scan → show count + confirm button; second click → queue
let _pendingTabJobs = null;

async function queueAllTabs() {
  const btn  = document.getElementById("all-tabs-btn");
  const info = document.getElementById("tabs-status");

  // Second step: user confirmed — queue what was found
  if (_pendingTabJobs) {
    const jobs = _pendingTabJobs;
    _pendingTabJobs = null;
    if (btn) { btn.textContent = "⚡ From all open tabs"; btn.disabled = true; }
    if (info) info.textContent = `Queuing ${jobs.length} item(s)…`;
    let started = 0;
    for (const j of jobs) {
      await new Promise(resolve =>
        chrome.runtime.sendMessage({ type: "DOWNLOAD", url: j.url, dlType: j.dlType }, () => {
          started++; resolve();
        })
      );
    }
    if (info) info.textContent = `Queued ${started} download(s).`;
    if (btn) btn.disabled = false;
    setTimeout(loadList, 1000);
    return;
  }

  // First step: scan all tabs
  if (btn) btn.disabled = true;
  if (info) info.textContent = "Scanning…";

  try {
    const tabs = await chrome.tabs.query({});
    const seen = new Set();
    const jobs = [];

    for (const tab of tabs) {
      if (!tab.url?.startsWith("http")) continue;

      const sniffed = await new Promise(resolve =>
        chrome.runtime.sendMessage({ type: "GET_SNIFFED_MEDIA", tabId: tab.id },
          r => resolve(r?.urls || []))
      );
      for (const u of sniffed) {
        if (!seen.has(u)) { seen.add(u); jobs.push({ url: u, dlType: "auto" }); }
      }

      await new Promise(resolve => {
        try {
          chrome.tabs.sendMessage(tab.id, { type: "SCAN_LINKS" }, res => {
            for (const lnk of (res?.links || [])) {
              if (!seen.has(lnk.url)) {
                seen.add(lnk.url);
                jobs.push({ url: lnk.url, dlType: lnk.ext === "torrent" ? "torrent" : "auto" });
              }
            }
            resolve();
          });
        } catch { resolve(); }
      });
    }

    if (!jobs.length) {
      if (info) info.textContent = "Nothing found in open tabs.";
      if (btn) btn.disabled = false;
      return;
    }

    // Show count and wait for confirmation click
    _pendingTabJobs = jobs;
    if (info) info.textContent = `Found ${jobs.length} item(s) — click again to queue`;
    if (btn) { btn.textContent = `✓ Queue ${jobs.length} items`; btn.disabled = false; }

    // Auto-reset after 10s if user doesn't confirm
    setTimeout(() => {
      if (_pendingTabJobs) {
        _pendingTabJobs = null;
        if (btn) { btn.textContent = "⚡ From all open tabs"; btn.disabled = false; }
        if (info) info.textContent = "";
      }
    }, 10000);

  } catch (e) {
    if (info) info.textContent = `Error: ${e.message}`;
    if (btn) btn.disabled = false;
  }
}

function toggleAdv() {
  const row = document.getElementById("server-row");
  const arr = document.getElementById("adv-arrow");
  if (!row) return;
  const open = row.style.display !== "none";
  row.style.display = open ? "none" : "flex";
  if (arr) arr.textContent = open ? "▸" : "▾";
}

function _updateSpeedBar(jobs, inetBps) {
  const appSpeed = jobs.reduce((s, j) => s + (+j.speed_bytes_per_sec || 0), 0);
  const active   = jobs.filter(j => j.status === "running").length;
  const inet     = document.getElementById("inet-label");
  const lbl      = document.getElementById("speed-label");
  const dot      = document.getElementById("speed-dot");
  const sub      = document.getElementById("speed-sub");
  // Only display internet speed when the probe has a meaningful result (> 1 Mbps threshold)
  const capMeaningful = inetBps > 125000;  // > 1 Mbps in bytes/s
  if (inet) inet.textContent = capMeaningful ? "↓ " + fmtMbps(inetBps) : "↓ -- Mbps";
  if (lbl)  lbl.textContent  = active > 0 ? "⬇ " + fmt(appSpeed) : "⬇ 0 B/s";
  if (dot)  dot.className    = "speed-dot" + (active > 0 ? " active" : "");
  if (sub)  sub.textContent  = active > 0 ? `${active} downloading` : "No active downloads";
}

async function _pollNetstats() {
  try {
    const data = await _fetch(`${_server}/api/netstats`).then(r => r.json());
    const inet = document.getElementById("inet-label");
    // capacity_bps is the probed speed — only show if it's a real measurement (> 1 Mbps)
    const cap = data.capacity_bps || 0;
    if (inet) inet.textContent = cap > 125000 ? "↓ " + fmtMbps(cap) : "↓ -- Mbps";
    const dot = document.getElementById("speed-dot");
    if (dot) dot.className = "speed-dot" + (cap > 125000 ? " active" : "");
  } catch (_) {}
}

function fmt(b) {
  if (b >= 1048576) return (b / 1048576).toFixed(2) + " MB/s";
  if (b >= 1024)    return (b / 1024).toFixed(1)    + " KB/s";
  return Math.round(b) + " B/s";
}

function fmtMbps(bps) {
  const mbps = (bps * 8) / 1_000_000;
  if (mbps >= 1) return mbps.toFixed(1) + " Mbps";
  return ((bps * 8) / 1000).toFixed(0) + " Kbps";
}

function fmtEta(secs) {
  if (!secs || !isFinite(secs) || secs <= 0) return "";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  if (s > 5) return `${s}s`;
  return "< 5s";
}

function setMsg(msg) {
  const el = document.getElementById("status-msg");
  if (el) { el.textContent = msg; setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 4000); }
}

function esc(v) {
  return String(v ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
