/* Reminal Download Manager — frontend */
"use strict";

const API = "";  // same origin; change to "http://localhost:7000" if hosting remotely

let _caps = {};
let _activePolls = {};
let _stagedIds = new Set();
let _savedDir = localStorage.getItem("LUMIDM.saveDir") || "";
let _selectMode = false;
let _selected = new Set();
let _lastTotalSpeed = 0;

// Video URL pattern for auto-detection
const _VIDEO_HOSTS = /youtube\.com|youtu\.be|vimeo\.com|tiktok\.com|twitter\.com|x\.com|instagram\.com|dailymotion\.com|twitch\.tv|facebook\.com|bilibili\.com|nicovideo\.jp|soundcloud\.com/i;

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", async () => {
  const sdEl     = document.getElementById("save-dir");
  const settDirEl = document.getElementById("settings-dir");
  if (sdEl)      sdEl.value     = _savedDir;
  if (settDirEl) settDirEl.value = _savedDir;

  // Nav
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  // Tab bar
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Direct download
  document.getElementById("direct-start")?.addEventListener("click", startDirect);
  document.getElementById("direct-urls")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && e.ctrlKey) startDirect();
  });

  // Torrent
  document.getElementById("torrent-start")?.addEventListener("click", startTorrent);
  document.getElementById("torrent-url")?.addEventListener("keydown", e => {
    if (e.key === "Enter") startTorrent();
  });

  // Video
  document.getElementById("video-start")?.addEventListener("click", startVideo);
  document.getElementById("video-url")?.addEventListener("keydown", e => {
    if (e.key === "Enter") startVideo();
  });

  // Grab
  document.getElementById("grab-scan")?.addEventListener("click", grabLinks);
  document.getElementById("grab-url")?.addEventListener("keydown", e => {
    if (e.key === "Enter") grabLinks();
  });

  // Batch crawl
  document.getElementById("batch-scan-btn")?.addEventListener("click",  () => batchCrawl(false));
  document.getElementById("batch-start-btn")?.addEventListener("click", () => batchCrawl(true));
  document.getElementById("batch-url")?.addEventListener("keydown", e => {
    if (e.key === "Enter") batchCrawl(false);
  });

  // Toolbar
  document.getElementById("refresh-btn")?.addEventListener("click", loadDownloads);
  document.getElementById("clear-btn")?.addEventListener("click", clearDone);
  document.getElementById("pause-all-btn")?.addEventListener("click", pauseAll);
  document.getElementById("resume-all-btn")?.addEventListener("click", resumeAll);
  document.getElementById("cancel-all-btn")?.addEventListener("click", cancelAll);
  document.getElementById("select-btn")?.addEventListener("click", toggleSelectMode);

  // Load all settings from server on startup (persisted across restarts)
  (async () => {
    try {
      const s = await api("GET", "/api/settings");
      if (s.default_dir) {
        _savedDir = s.default_dir;
        localStorage.setItem("LUMIDM.saveDir", _savedDir);
        const sd  = document.getElementById("save-dir");
        const sde = document.getElementById("settings-dir");
        if (sd)  sd.value  = _savedDir;
        if (sde) sde.value = _savedDir;
      }
      if (s.completion_action) {
        const el = document.getElementById("completion-action");
        if (el) el.value = s.completion_action;
      }
      if (s.default_connections) {
        const el = document.getElementById("settings-connections");
        if (el) el.value = s.default_connections;
      }
    } catch (_) {}
  })();

  // Settings — max concurrent
  document.getElementById("settings-concurrent-save")?.addEventListener("click", async () => {
    const val = parseInt(document.getElementById("settings-concurrent")?.value || "0", 10);
    if (!val || val < 1) { setStatus("settings-concurrent-status", "Enter a number 1–128."); return; }
    try {
      const r = await api("POST", "/api/settings/concurrent", { value: val });
      document.getElementById("settings-concurrent").value = r.max_concurrent;
      if (_caps) _caps.max_concurrent = r.max_concurrent;
      renderCapDetail();
      setStatus("settings-concurrent-status", "Saved.");
    } catch (_) { setStatus("settings-concurrent-status", "Failed to save."); }
  });

  // Settings — default connections per download
  document.getElementById("settings-connections-save")?.addEventListener("click", async () => {
    const val = parseInt(document.getElementById("settings-connections")?.value || "0", 10);
    if (!val || val < 1) { setStatus("settings-connections-status", "Enter a number 1–64."); return; }
    try {
      const r = await api("POST", "/api/settings/connections", { value: val });
      document.getElementById("settings-connections").value = r.default_connections;
      setStatus("settings-connections-status", "Saved.");
    } catch (_) { setStatus("settings-connections-status", "Failed to save."); }
  });

  // Settings — completion action
  document.getElementById("completion-action-save")?.addEventListener("click", async () => {
    const action = document.getElementById("completion-action")?.value || "none";
    await api("POST", "/api/settings/completion-action", { action });
    setStatus("completion-action-status", action === "none" ? "Saved — no action after downloads." : `Saved — will ${action} when all downloads finish.`);
  });

  // Settings — save folder (persists to server so extension picks it up too)
  document.getElementById("settings-save")?.addEventListener("click", async () => {
    const dir = document.getElementById("settings-dir")?.value.trim() || "";
    _savedDir = dir;
    localStorage.setItem("LUMIDM.saveDir", dir);
    const sdEl2 = document.getElementById("save-dir");
    if (sdEl2) sdEl2.value = dir;
    try {
      await api("POST", "/api/settings/default-dir", { dir });
      setStatus("settings-status", "Saved.");
    } catch (_) { setStatus("settings-status", "Saved locally."); }
  });

  // Save dir sync
  document.getElementById("save-dir")?.addEventListener("change", e => {
    _savedDir = e.target.value.trim();
    localStorage.setItem("LUMIDM.saveDir", _savedDir);
    const sde = document.getElementById("settings-dir");
    if (sde) sde.value = _savedDir;
  });

  // Retry all failed
  document.getElementById("retry-all-btn")?.addEventListener("click", async () => {
    await api("POST", "/api/downloads/retry-all").catch(() => {});
    loadDownloads();
  });

  _caps = await loadCapabilities();
  await loadDownloads();

  setInterval(loadDownloads, 2000);
});

// ── Navigation ────────────────────────────────────────────────────────────────

function switchView(name) {
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("is-active", v.id === `view-${name}`));
  document.querySelectorAll(".nav-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.view === name));
  if (name === "downloads") loadDownloads();
  if (name === "settings")  renderCapDetail();
}

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p =>
    p.classList.toggle("is-active", p.id === `panel-${name}`));
}

// ── Capabilities ──────────────────────────────────────────────────────────────

async function loadCapabilities() {
  try {
    const c = await api("GET", "/api/capabilities");
    const torrentTab = document.getElementById("tab-torrent");
    const videoTab   = document.getElementById("tab-video");
    if (torrentTab) {
      torrentTab.disabled = !c.torrent;
      torrentTab.title = c.torrent ? "" : "Install lbry-libtorrent or aria2c to enable";
    }
    if (videoTab) {
      videoTab.disabled = !c.video;
      videoTab.title = c.video ? "" : "Run: pip install yt-dlp";
    }
    const capsEl = document.getElementById("caps-display");
    if (capsEl) {
      capsEl.innerHTML = [
        { label: "HTTP/FTP", on: c.http },
        { label: "Torrent",  on: c.torrent },
        { label: "Video",    on: c.video },
      ].map(x => `<div class="cap-item"><span class="cap-dot ${x.on ? "on" : "off"}"></span>${x.label}</div>`).join("");
    }
    return c;
  } catch (_) { return {}; }
}

function renderCapDetail() {
  const el = document.getElementById("caps-detail");
  if (!el || !_caps) return;
  // Pre-fill concurrent input with current value
  const ci = document.getElementById("settings-concurrent");
  if (ci && _caps.max_concurrent) ci.value = _caps.max_concurrent;
  const rows = [
    { label: "HTTP / HTTPS downloads",     val: _caps.http },
    { label: "FTP downloads",              val: _caps.ftp },
    { label: "Torrent / Magnet",           val: _caps.torrent, note: _caps.torrent_lib },
    { label: "Video platforms (yt-dlp)",   val: _caps.video },
    { label: "Max simultaneous downloads", val: _caps.max_concurrent, text: true },
  ];
  el.innerHTML = rows.map(r => `
    <div class="cap-row">
      <span>${r.label}</span>
      ${r.text
        ? `<span class="cap-chip yes">${r.val}</span>`
        : `<span class="cap-chip ${r.val ? "yes" : "no"}">${r.val ? "Available" : "Not installed"}</span>`}
      ${r.note ? `<span style="font-size:11px;color:var(--muted)">(${r.note})</span>` : ""}
    </div>`).join("");
}

// ── Downloads list ────────────────────────────────────────────────────────────

async function loadDownloads() {
  const banner = document.getElementById("server-banner");
  try {
    const data = await api("GET", "/api/downloads?limit=100");
    if (banner) banner.style.display = "none";
    const jobs = Array.isArray(data.downloads) ? data.downloads : [];
    const list = document.getElementById("dl-list");
    if (!list) return;

    const staged  = jobs.filter(j => j.status === "staged");
    const visible = jobs.filter(j => j.status !== "staged");

    _showStagedModal(staged);

    const hasFailed = visible.some(j => j.status === "failed" || j.status === "cancelled");
    const retryBtn  = document.getElementById("retry-all-btn");
    if (retryBtn) retryBtn.style.display = hasFailed ? "inline-flex" : "none";

    if (!visible.length) {
      list.innerHTML = `<div class="empty-state">No downloads yet. Click <strong>＋ New download</strong> to start.</div>`;
      _activePolls = {};
      return;
    }
    _activePolls = {};
    visible.forEach(j => {
      if (!["completed", "failed", "cancelled", "paused"].includes(j.status)) {
        _activePolls[j.id] = true;
      }
    });
    if (staged.length) _activePolls["__staged__"] = true;

    // Live speed in Downloads header
    _lastTotalSpeed = visible.reduce((s, j) => s + (+j.speed_bytes_per_sec || 0), 0);
    _updateLiveSpeed(_lastTotalSpeed);

    const hdr = `<div class="dl-list-hdr">
      <span>${visible.length} download${visible.length !== 1 ? "s" : ""}</span>
      ${_selectMode ? `<label style="font-size:11px;color:var(--muted);cursor:pointer"><input type="checkbox" id="sel-all" onchange="selAll(this.checked)"> Select all</label>` : ""}
    </div>`;
    list.innerHTML = hdr + visible.map(cardHtml).join("");
    _updateSelBar();
  } catch (_) {
    const banner = document.getElementById("server-banner");
    if (banner) banner.style.display = "flex";
  }
}

// ── Staged download confirmation modal ────────────────────────────────────────

function _showStagedModal(staged) {
  const overlay = document.getElementById("staged-overlay");
  if (!overlay) return;
  if (!staged.length) {
    overlay.style.display = "none";
    _stagedIds.clear();
    return;
  }
  // Only rebuild if set of staged IDs changed
  const newIds = new Set(staged.map(j => j.id));
  const changed = staged.some(j => !_stagedIds.has(j.id)) || _stagedIds.size !== newIds.size;
  _stagedIds = newIds;
  if (!changed && overlay.style.display !== "none") return;

  overlay.style.display = "flex";
  const list = document.getElementById("staged-list");
  if (!list) return;
  list.innerHTML = staged.map(j => {
    const szTxt = j.total_bytes > 0 ? ` · ${fmtBytes(j.total_bytes)}` : "";
    return `
    <div class="staged-item" id="si-${esc(j.id)}">
      <div class="staged-url" title="${esc(j.url)}">${esc(j.url.length > 70 ? j.url.slice(0,70)+"…" : j.url)}</div>
      <div class="staged-row">
        <label class="staged-label">Save as</label>
        <input class="staged-input" id="si-fn-${esc(j.id)}" value="${esc(j.filename || "")}" placeholder="filename" />
      </div>
      <div class="staged-row">
        <label class="staged-label">Folder</label>
        <input class="staged-input" id="si-td-${esc(j.id)}" value="${esc(j.target_dir || "")}" placeholder="download folder" />
        ${window.electronApp?.isElectron ? `<button class="prop-copy" style="flex-shrink:0;padding:2px 8px" onclick="pickStagedFolder('${esc(j.id)}')">📁</button>` : ''}
      </div>
      <div class="staged-meta">${esc(j.filename || "")}${szTxt}</div>
      <div class="staged-btns">
        <button class="btn-accent" onclick="confirmStaged('${esc(j.id)}')">▶ Start Download</button>
        <button class="btn-ghost" onclick="cancelStaged('${esc(j.id)}')">✕ Cancel</button>
      </div>
    </div>`;
  }).join('<hr class="staged-sep">');
}

window.pickStagedFolder = async (jid) => {
  const folder = await window.electronApp?.pickFolder();
  if (folder) {
    const el = document.getElementById(`si-td-${jid}`);
    if (el) el.value = folder;
  }
};

window.confirmStaged = async (jid) => {
  const fname = document.getElementById(`si-fn-${jid}`)?.value.trim() || "";
  const tdir  = document.getElementById(`si-td-${jid}`)?.value.trim() || "";
  await api("POST", `/api/downloads/${jid}/confirm`, { filename: fname, target_dir: tdir });
  // Persist folder choice
  if (tdir) {
    _savedDir = tdir;
    localStorage.setItem("LUMIDM.saveDir", tdir);
    api("POST", "/api/settings/default-dir", { dir: tdir }).catch(() => {});
    const sde = document.getElementById("settings-dir");
    const sd  = document.getElementById("save-dir");
    if (sde) sde.value = tdir;
    if (sd)  sd.value  = tdir;
  }
  loadDownloads();
  switchView("downloads");
  window.electronApp?.afterStagedConfirm?.();
};

window.cancelStaged = async (jid) => {
  await api("POST", `/api/downloads/${jid}/delete`);
  loadDownloads();
  window.electronApp?.afterStagedConfirm?.();
};

// ── Live speed indicators ─────────────────────────────────────────────────────

function _updateLiveSpeed(appBps) {
  const dlVal = document.getElementById("live-dl-val");
  const dot   = document.getElementById("live-speed-dot");
  if (dlVal) dlVal.textContent = "⬇ " + fmtSpeed(appBps);
  if (dot)   dot.className = "live-speed-dot" + (appBps > 1000 ? " active" : "");
}

function _fmtMbps(bps) {
  const mbps = (bps * 8) / 1_000_000;
  if (mbps >= 1) return mbps.toFixed(1) + " Mbps";
  return ((bps * 8) / 1000).toFixed(0) + " Kbps";
}

async function _pollNetstats() {
  try {
    const data = await api("GET", "/api/netstats");
    const bps  = data.capacity_bps || 0;   // only show real probed speed, never OS noise
    const val  = document.getElementById("live-speed-val");
    if (val) val.textContent = bps > 100000 ? _fmtMbps(bps) : "-- Mbps";
  } catch (_) {}
}

// ── Multi-select ──────────────────────────────────────────────────────────────

function toggleSelectMode() {
  _selectMode = !_selectMode;
  _selected.clear();
  const btn = document.getElementById("select-btn");
  if (btn) btn.classList.toggle("is-active", _selectMode);
  loadDownloads();
}

function selAll(checked) {
  document.querySelectorAll(".dl-sel-cb").forEach(cb => {
    cb.checked = checked;
    if (checked) _selected.add(cb.dataset.id);
    else _selected.delete(cb.dataset.id);
  });
  _updateSelBar();
}

window.selToggle = (jid, checked) => {
  if (checked) _selected.add(jid);
  else _selected.delete(jid);
  _updateSelBar();
  const allCb = document.getElementById("sel-all");
  if (allCb) allCb.checked = document.querySelectorAll(".dl-sel-cb:not(:checked)").length === 0;
};

function selClear() {
  _selected.clear();
  document.querySelectorAll(".dl-sel-cb").forEach(cb => cb.checked = false);
  const allCb = document.getElementById("sel-all");
  if (allCb) allCb.checked = false;
  _updateSelBar();
}

function _updateSelBar() {
  const bar = document.getElementById("sel-bar");
  const cnt = document.getElementById("sel-count");
  if (!bar) return;
  if (!_selectMode || _selected.size === 0) {
    bar.style.display = "none";
    return;
  }
  bar.style.display = "flex";
  if (cnt) cnt.textContent = `${_selected.size} selected`;
}

async function selAction(action) {
  const ids = [..._selected];
  if (!ids.length) return;

  if (action === "delete") { _showDeleteDialog(ids); return; }

  // Filter to ids where the action is valid based on current card status
  const validStates = {
    pause:  ["running", "queued", "probing"],
    resume: ["paused", "failed"],
    cancel: ["running", "queued", "probing", "pausing", "cancelling", "staged"],
  };
  const allowed = validStates[action] || [];
  const eligible = ids.filter(id => {
    const card = document.getElementById(`card-${id}`);
    return card ? allowed.includes(card.dataset.status) : true;
  });

  if (!eligible.length) {
    // Nothing to do — tell the user why
    const label = action === "pause" ? "running" : action === "resume" ? "paused/failed" : "active";
    _showToast(`No ${label} downloads selected`);
    return;
  }

  for (const id of eligible) {
    if (action === "pause")  await api("POST", `/api/downloads/${id}/pause`).catch(()=>{});
    if (action === "resume") await api("POST", `/api/downloads/${id}/resume`).catch(()=>{});
    if (action === "cancel") await api("POST", `/api/downloads/${id}/cancel`).catch(()=>{});
  }
  _selected.clear();
  loadDownloads();
}

function _showToast(msg, ms = 2500) {
  let t = document.getElementById("LUMIDM-toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "LUMIDM-toast";
    t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
      "background:#23262f;color:#e8eaed;padding:8px 18px;border-radius:8px;" +
      "font-size:13px;z-index:99999;pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,.4);";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = "1";
  clearTimeout(t._to);
  t._to = setTimeout(() => { t.style.opacity = "0"; }, ms);
}

async function clearDone() {
  await api("POST", "/api/downloads/clear");
  loadDownloads();
}

async function pauseAll() {
  await api("POST", "/api/downloads/pause-all");
  loadDownloads();
}

async function resumeAll() {
  await api("POST", "/api/downloads/resume-all");
  loadDownloads();
}

async function cancelAll() {
  if (!confirm("Cancel all active downloads?")) return;
  await api("POST", "/api/downloads/cancel-all");
  loadDownloads();
}

// ── New downloads ─────────────────────────────────────────────────────────────

async function startDirect() {
  const textarea  = document.getElementById("direct-urls");
  const rawLines  = (textarea?.value || "").split(/\n+/).map(u => u.trim()).filter(Boolean);
  const urls      = rawLines.filter(u =>
    u.startsWith("http") || u.startsWith("https") || u.startsWith("ftp://") ||
    u.startsWith("magnet:") || u.toLowerCase().endsWith(".torrent"));

  if (!urls.length) {
    setStatus("direct-status", "Paste at least one URL (http/https/ftp/magnet).");
    return;
  }

  const conns    = parseInt(document.getElementById("direct-conns")?.value  || "32", 10);
  const speedKB  = parseInt(document.getElementById("direct-speed")?.value  || "0",  10);
  const filename = (document.getElementById("direct-filename")?.value || "").trim();
  const overwrite = document.getElementById("direct-overwrite")?.checked || false;
  const saveDir   = (document.getElementById("save-dir")?.value || "").trim() || _savedDir;

  setStatus("direct-status", `Starting ${urls.length} download(s)…`);
  let started = 0;

  for (const url of urls) {
    try {
      const isTorrent = url.startsWith("magnet:") || url.toLowerCase().endsWith(".torrent");
      const isVideo   = !isTorrent && _caps.video && _VIDEO_HOSTS.test(url);
      const body      = { url };
      if (saveDir) body.target_dir = saveDir;

      if (isTorrent) {
        await api("POST", "/api/downloads/torrent", body);
      } else if (isVideo) {
        body.format_id = document.getElementById("video-fmt")?.value || "bestvideo+bestaudio/best";
        await api("POST", "/api/downloads/video", body);
      } else {
        body.connections  = conns;
        body.overwrite    = overwrite;
        if (speedKB > 0)            body.max_speed_bps = speedKB * 1024;
        if (filename && urls.length === 1) body.filename = filename;
        await api("POST", "/api/downloads/start", body);
      }
      started++;
    } catch (e) {
      setStatus("direct-status", `Error: ${e.message}`);
    }
  }

  if (textarea) textarea.value = "";
  const filenameEl = document.getElementById("direct-filename");
  if (filenameEl) filenameEl.value = "";
  setStatus("direct-status", `Started ${started} download(s).`);
  loadDownloads();
  switchView("downloads");
}

async function startTorrent() {
  const urlEl = document.getElementById("torrent-url");
  const url   = (urlEl?.value || "").trim();
  if (!url) { setStatus("torrent-status", "Enter a magnet link or .torrent URL."); return; }
  const saveDir = (document.getElementById("save-dir")?.value || "").trim() || _savedDir;
  const conns   = parseInt(document.getElementById("torrent-conns")?.value || "32", 10);
  setStatus("torrent-status", "Starting torrent…");
  try {
    const body = { url, connections: conns };
    if (saveDir) body.target_dir = saveDir;
    const job = await api("POST", "/api/downloads/torrent", body);
    if (job.error) { setStatus("torrent-status", `Error: ${job.error}`); return; }
    if (urlEl) urlEl.value = "";
    setStatus("torrent-status", `Torrent queued: ${job.filename || job.id}`);
    loadDownloads();
    switchView("downloads");
  } catch (e) { setStatus("torrent-status", `Failed: ${e.message}`); }
}

async function startVideo() {
  const urlEl = document.getElementById("video-url");
  const url   = (urlEl?.value || "").trim();
  if (!url.startsWith("http")) { setStatus("video-status", "Enter a valid URL."); return; }
  const saveDir = (document.getElementById("save-dir")?.value || "").trim() || _savedDir;
  setStatus("video-status", "Fetching video info…");
  try {
    const body = {
      url,
      format_id:  document.getElementById("video-fmt")?.value  || "bestvideo+bestaudio/best",
      audio_only: document.getElementById("video-mp3")?.checked  || false,
      subtitles:  document.getElementById("video-subs")?.checked || false,
    };
    if (saveDir) body.target_dir = saveDir;
    const job = await api("POST", "/api/downloads/video", body);
    if (job.error) { setStatus("video-status", `Error: ${job.error}`); return; }
    if (urlEl) urlEl.value = "";
    setStatus("video-status", "Video download queued.");
    loadDownloads();
    switchView("downloads");
  } catch (e) { setStatus("video-status", `Failed: ${e.message}`); }
}

async function grabLinks() {
  const urlEl     = document.getElementById("grab-url");
  const resultsEl = document.getElementById("grab-results");
  const url = (urlEl?.value || "").trim();
  if (!url.startsWith("http")) { setStatus("grab-status", "Enter a valid URL."); return; }
  setStatus("grab-status", "Scanning page for download links…");
  if (resultsEl) resultsEl.innerHTML = "";
  try {
    const data  = await api("POST", "/api/grab", { url });
    const links = Array.isArray(data.links) ? data.links : [];
    if (!links.length) { setStatus("grab-status", "No downloadable files found."); return; }
    setStatus("grab-status", `Found ${links.length} file(s). Select and download.`);
    if (resultsEl) {
      resultsEl.innerHTML = `
        <div class="grab-toolbar">
          <label><input type="checkbox" id="grab-all" checked> Select all</label>
          <button class="primary-btn" id="grab-dl-sel">⬇ Download selected</button>
        </div>
        <div class="grab-list">
          ${links.map(l => `
            <label class="grab-item">
              <input type="checkbox" class="grab-cb" data-url="${esc(l.url)}" data-fname="${esc(l.filename)}" checked>
              <span class="grab-ext">${esc(l.ext)}</span>
              <span class="grab-name">${esc(l.filename)}</span>
            </label>`).join("")}
        </div>`;
      document.getElementById("grab-all")?.addEventListener("change", e => {
        resultsEl.querySelectorAll(".grab-cb").forEach(cb => { cb.checked = e.target.checked; });
      });
      document.getElementById("grab-dl-sel")?.addEventListener("click", async () => {
        const checked = [...resultsEl.querySelectorAll(".grab-cb:checked")];
        if (!checked.length) return;
        const saveDir = (document.getElementById("save-dir")?.value || "").trim() || _savedDir;
        setStatus("grab-status", `Starting ${checked.length} download(s)…`);
        for (const cb of checked) {
          const body = { url: cb.dataset.url };
          if (saveDir) body.target_dir = saveDir;
          try { await api("POST", "/api/downloads/start", body); } catch (_) {}
        }
        setStatus("grab-status", `Started ${checked.length} download(s).`);
        loadDownloads();
        switchView("downloads");
      });
    }
  } catch (e) { setStatus("grab-status", `Failed: ${e.message}`); }
}

// ── Batch crawl ───────────────────────────────────────────────────────────────

async function batchCrawl(startImmediately) {
  const url = (document.getElementById("batch-url")?.value || "").trim();
  if (!url.startsWith("http")) { setStatus("batch-status", "Enter a valid URL."); return; }

  const maxPages = parseInt(document.getElementById("batch-pages")?.value || "10", 10);
  const incVid   = document.getElementById("batch-inc-videos")?.checked ?? true;
  const incFiles = document.getElementById("batch-inc-files")?.checked  ?? true;
  const saveDir  = (document.getElementById("save-dir")?.value || "").trim() || _savedDir;
  const results  = document.getElementById("batch-results");
  if (results) results.innerHTML = "";

  setStatus("batch-status", `Crawling — scanning up to ${maxPages} page(s)…`);

  try {
    const data  = await api("POST", "/api/batch/crawl", {
      url, max_pages: maxPages, include_videos: incVid, include_files: incFiles,
    });
    const links = Array.isArray(data.links) ? data.links : [];
    const pages = data.pages_crawled || 1;

    if (!links.length) {
      setStatus("batch-status", `Scanned ${pages} page(s) — nothing found.`);
      return;
    }

    setStatus("batch-status",
      `Found ${links.length} item(s) across ${pages} page(s).`
      + (startImmediately ? " Queuing…" : " Select what to download."));

    if (startImmediately) {
      // Queue everything at once
      const body = { url, max_pages: maxPages, include_videos: incVid, include_files: incFiles };
      if (saveDir) body.target_dir = saveDir;
      const r = await api("POST", "/api/batch/start", body);
      setStatus("batch-status", `Started ${r.started} download(s).`
        + (r.errors ? ` (${r.errors} failed)` : ""));
      loadDownloads();
      switchView("downloads");
      return;
    }

    // Show checkbox list for selective queuing
    const typeIcon = { video: "▶", torrent: "⇅", http: "⬇" };
    if (results) {
      results.innerHTML = `
        <div class="grab-toolbar" style="margin-top:12px">
          <label><input type="checkbox" id="batch-all" checked> Select all
            <span style="color:var(--muted);font-size:10px;margin-left:6px">${links.length} items · ${pages} page(s) crawled</span>
          </label>
          <button class="primary-btn" id="batch-dl-sel" style="margin-left:auto">⚡ Queue selected</button>
        </div>
        <div class="grab-list" style="margin-top:6px">
          ${links.map((lnk, i) => `
            <label class="grab-item">
              <input class="grab-cb" type="checkbox" checked
                     data-url="${esc(lnk.url)}" data-type="${esc(lnk.type || 'http')}">
              <span class="grab-icon">${typeIcon[lnk.type] || "⬇"}</span>
              <span class="grab-name" title="${esc(lnk.url)}">${esc(lnk.filename || lnk.url)}</span>
              ${lnk.from_page ? `<span class="grab-ext" title="${esc(lnk.from_page)}">p.${links.slice(0,i+1).filter(x=>x.from_page===lnk.from_page).length===1?data.pages.indexOf(lnk.from_page)+1:"…"}</span>` : ""}
            </label>`).join("")}
        </div>`;

      document.getElementById("batch-all")?.addEventListener("change", e => {
        results.querySelectorAll(".grab-cb").forEach(cb => cb.checked = e.target.checked);
      });

      document.getElementById("batch-dl-sel")?.addEventListener("click", async () => {
        const checked = [...results.querySelectorAll(".grab-cb:checked")];
        if (!checked.length) return;
        const urls  = checked.map(cb => cb.dataset.url);
        const types = Object.fromEntries(checked.map(cb => [cb.dataset.url, cb.dataset.type]));
        const body  = { url, urls, types, max_pages: maxPages };
        if (saveDir) body.target_dir = saveDir;
        setStatus("batch-status", `Queuing ${urls.length} item(s)…`);
        const r = await api("POST", "/api/batch/start", body);
        setStatus("batch-status", `Started ${r.started} download(s).`
          + (r.errors ? ` (${r.errors} failed)` : ""));
        loadDownloads();
        switchView("downloads");
      });
    }
  } catch (e) { setStatus("batch-status", `Failed: ${e.message}`); }
}

// ── Download card actions ─────────────────────────────────────────────────────

window.dlPause  = jid => api("POST", `/api/downloads/${jid}/pause`).then(() => loadDownloads());
window.dlResume = jid => api("POST", `/api/downloads/${jid}/resume`).then(() => loadDownloads());
window.dlCancel = jid => api("POST", `/api/downloads/${jid}/cancel`).then(() => loadDownloads());
window.dlDelete = jid => _showDeleteDialog([jid]);

function _showDeleteDialog(jids) {
  document.getElementById("del-dialog")?.remove();
  const multi = jids.length > 1;

  const overlay = document.createElement("div");
  overlay.id = "del-dialog";
  overlay.className = "del-dialog-overlay";

  const box = document.createElement("div");
  box.className = "del-dialog-box";

  const title = document.createElement("div");
  title.className = "del-dialog-title";
  title.textContent = multi ? `Remove ${jids.length} downloads?` : "Remove download?";

  const mkBtn = (cls, label, sub) => {
    const btn = document.createElement("button");
    btn.className = cls;
    btn.textContent = label;
    if (sub) {
      const s = document.createElement("span");
      s.className = "del-opt-sub";
      s.textContent = sub;
      btn.appendChild(s);
    }
    return btn;
  };

  const btnKeep = mkBtn("del-opt", "Remove from list only",
    `Keep the file${multi ? "s" : ""} on disk`);
  const btnDel  = mkBtn("del-opt del-opt-red", `Remove + delete file${multi ? "s" : ""}`,
    "Permanently delete from disk");
  const btnCnl  = mkBtn("del-opt del-opt-cancel", "Cancel", null);

  btnKeep.addEventListener("click", () => _confirmDelete(jids, false));
  btnDel .addEventListener("click", () => _confirmDelete(jids, true));
  btnCnl .addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });

  box.append(title, btnKeep, btnDel, btnCnl);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
}

window._confirmDelete = async (jids, deleteFile) => {
  document.getElementById("del-dialog")?.remove();
  for (const jid of jids)
    await api("POST", `/api/downloads/${jid}/delete`, { delete_file: deleteFile }).catch(()=>{});
  _selected.clear();
  loadDownloads();
};
window.dlRetry  = jid => api("POST", `/api/downloads/${jid}/retry`).then(() => { loadDownloads(); switchView("downloads"); });

window.dlOpen = async jid => {
  const r = await api("POST", `/api/downloads/${jid}/open`);
  if (r.error) alert(`Cannot open folder: ${r.error}`);
};

window.dlToggleProps = jid => {
  const el = document.getElementById(`props-${jid}`);
  if (el) el.style.display = el.style.display === "none" ? "" : "none";
};

window.dlCopyUrl = async url => {
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
  } catch (_) {
    const el = document.createElement("textarea");
    el.value = url; el.style.position = "fixed"; el.style.opacity = "0";
    document.body.appendChild(el); el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }
};

window.dlVerify = async jid => {
  const hash = prompt("Paste expected hash (MD5 / SHA1 / SHA256 / SHA512):");
  if (!hash) return;
  const algo = hash.length >= 128 ? "sha512" : hash.length >= 64 ? "sha256"
             : hash.length === 40 ? "sha1" : "md5";
  const r = await api("POST", `/api/downloads/${jid}/verify`, { hash, algo });
  if (r.status === "ok")
    alert(`\u2713 Checksum OK (${r.algo.toUpperCase()})\n${r.actual}`);
  else if (r.status === "mismatch")
    alert(`\u2717 MISMATCH!\nExpected: ${r.expected}\nActual:   ${r.actual}`);
  else
    alert(`Error: ${r.error || r.status}`);
};

// ── Card HTML ─────────────────────────────────────────────────────────────────

function cardHtml(j) {
  const { id, type, url = "", filename, status, progress_percent,
          downloaded_bytes, total_bytes, speed_bytes_per_sec, error } = j;

  const pct   = Math.min(100, Number(progress_percent || 0));
  const dlSz  = fmtBytes(Number(downloaded_bytes || 0));
  const totSz = Number(total_bytes) > 0 ? fmtBytes(Number(total_bytes)) : null;
  const spd   = Number(speed_bytes_per_sec) > 100 ? fmtSpeed(speed_bytes_per_sec) : "";
  const eta   = ["completed","failed","cancelled","paused"].includes(status) ? ""
              : fmtETA(Number(downloaded_bytes), Number(total_bytes), Number(speed_bytes_per_sec));

  const icon = status === "completed" ? "✓" : status === "failed" ? "✗"
             : status === "cancelled" ? "⊘" : status === "paused" ? "⏸"
             : type === "torrent" ? "⇅" : type === "video" ? "▶" : "⬇";

  const cls = status === "completed" ? "done" : status === "failed" ? "error"
            : status === "cancelled" ? "cancelled" : status === "paused" ? "paused" : "running";

  const terminal = ["completed","failed","cancelled"].includes(status);
  const paused   = status === "paused";
  const failed   = status === "failed" || status === "cancelled";

  const typeBadge = type && type !== "http"
    ? `<span class="dl-type">${esc(type)}</span>` : "";

  const pauseBtn  = (!terminal && !paused)
    ? `<button class="dl-btn blue"   onclick="dlPause('${id}')"  title="Pause">⏸</button>`
    : paused
      ? `<button class="dl-btn green"  onclick="dlResume('${id}')" title="Resume">▶</button>` : "";

  const cancelBtn = terminal ? "" :
    `<button class="dl-btn red"    onclick="dlCancel('${id}')" title="Cancel">✕</button>`;

  const retryBtn  = failed ?
    `<button class="dl-btn orange" onclick="dlRetry('${id}')"  title="Retry">↺</button>` : "";

  const openBtn   = terminal ?
    `<button class="dl-btn blue"   onclick="dlOpen('${id}')"   title="Open file location">⤴</button>` : "";

  const deleteBtn = terminal ?
    `<button class="dl-btn red"    onclick="dlDelete('${id}')" title="Delete file">🗑</button>` : "";

  const verifyBtn = status === "completed" ?
    `<button class="dl-btn green"  onclick="dlVerify('${id}')" title="Verify checksum">✔</button>` : "";

  const copyBtn   = url ?
    `<button class="dl-btn"        onclick="dlCopyUrl('${esc(url)}')" title="Copy URL">⎘</button>` : "";

  const infoBtn   = `<button class="dl-btn" onclick="dlToggleProps('${id}')" title="Properties">ℹ</button>`;

  const stats = [
    `${pct.toFixed(1)}%`,
    totSz ? `${dlSz} / ${totSz}` : (Number(downloaded_bytes) > 0 ? dlSz : ""),
    spd, eta,
  ].filter(Boolean).join(" · ");

  const selCb = _selectMode
    ? `<input type="checkbox" class="dl-sel-cb" data-id="${id}"
         ${_selected.has(id) ? "checked" : ""}
         onchange="selToggle('${id}', this.checked)"
         style="margin-right:6px;flex-shrink:0;cursor:pointer;accent-color:var(--accent)">`
    : "";

  return `
    <div class="dl-card ${cls}" id="card-${id}" data-status="${esc(status)}">
      <div class="dl-hdr">
        ${selCb}<span class="dl-icon">${icon}</span>
        <span class="dl-name" title="${esc(filename || id)}">${esc(filename || id)}</span>
        ${typeBadge}
        <span class="dl-status-badge">${esc(status)}</span>
        <div class="dl-actions">${copyBtn}${infoBtn}${verifyBtn}${openBtn}${retryBtn}${pauseBtn}${cancelBtn}${deleteBtn}</div>
      </div>
      <div class="dl-bar-wrap"><div class="dl-bar" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="dl-stats">${stats}</div>
      ${error ? `<div class="dl-error" title="${esc(error)}">${esc(error)}</div>` : ""}
      <div class="dl-props" id="props-${id}" style="display:none">
        ${j.path     ? `<div class="prop-row"><span class="prop-label">Path</span><span class="prop-val" title="${esc(j.path)}">${esc(j.path)}</span><button class="prop-copy" onclick="dlCopyUrl('${esc(j.path)}')" title="Copy path">⎘</button><button class="prop-open" onclick="dlOpen('${id}')" title="Open in Explorer">⤴</button></div>` : ""}
        ${url        ? `<div class="prop-row"><span class="prop-label">URL</span><span class="prop-val" title="${esc(url)}">${esc(url)}</span><button class="prop-copy" onclick="dlCopyUrl('${esc(url)}')" title="Copy URL">⎘</button></div>` : ""}
        ${j.total_bytes > 0 ? `<div class="prop-row"><span class="prop-label">Size</span><span class="prop-val">${esc(fmtBytes(Number(j.total_bytes)))}</span></div>` : ""}
        ${j.target_dir ? `<div class="prop-row"><span class="prop-label">Folder</span><span class="prop-val">${esc(j.target_dir)}</span></div>` : ""}
      </div>
    </div>`;
}

// ── Formatting ────────────────────────────────────────────────────────────────

function fmtBytes(b) {
  if (b >= 1099511627776) return `${(b / 1099511627776).toFixed(2)} TB`;
  if (b >= 1073741824)    return `${(b / 1073741824).toFixed(2)} GB`;
  if (b >= 1048576)       return `${(b / 1048576).toFixed(2)} MB`;
  if (b >= 1024)          return `${(b / 1024).toFixed(0)} KB`;
  return `${Math.round(b)} B`;
}

function fmtSpeed(bps) {
  if (bps >= 1048576) return `${(bps / 1048576).toFixed(1)} MB/s`;
  if (bps >= 1024)    return `${(bps / 1024).toFixed(0)} KB/s`;
  return `${Math.round(bps)} B/s`;
}

// ── Internet speed test ───────────────────────────────────────────────────────

let _speedTestRunning = false;

async function runSpeedTest() {
  if (_speedTestRunning) return;
  _speedTestRunning = true;

  const resultEl = document.getElementById("speedtest-result");
  const btnEl    = document.getElementById("speedtest-btn");
  const barEl    = document.getElementById("speedtest-bar");

  if (resultEl) resultEl.textContent  = "Testing…";
  if (btnEl)    btnEl.disabled        = true;
  if (barEl)    barEl.style.width     = "0%";

  // Animate bar while waiting
  let pct = 0;
  const anim = setInterval(() => {
    pct = Math.min(pct + 2, 90);
    if (barEl) barEl.style.width = pct + "%";
  }, 400);

  try {
    const data = await api("GET", "/api/speedtest");
    clearInterval(anim);
    if (barEl) barEl.style.width = "100%";
    if (data.error) {
      if (resultEl) resultEl.textContent = "Error: " + data.error;
    } else {
      const mbps = data.mbps ?? (data.bps / 1_000_000).toFixed(2);
      if (resultEl) resultEl.innerHTML =
        `<span class="st-num">${mbps}</span> <span class="st-unit">Mbps</span>` +
        ` <span class="st-sub">(${fmtSpeed(data.bps)} download)</span>`;
    }
  } catch (e) {
    clearInterval(anim);
    if (resultEl) resultEl.textContent = "Speed test failed — server not reachable.";
  }

  if (btnEl) btnEl.disabled = false;
  _speedTestRunning = false;
  setTimeout(() => { if (barEl) barEl.style.width = "0%"; }, 3000);
}

function fmtETA(dl, total, bps) {
  if (!total || !bps || bps < 100) return "";
  const secs = Math.max(0, (total - dl)) / bps;
  if (!isFinite(secs) || secs <= 0) return "";
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = Math.floor(secs % 60);
  if (h > 0) return `${h}h ${m}m left`;
  if (m > 0) return `${m}m ${s}s left`;
  return `${s}s left`;
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function esc(v) {
  return String(v ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function setStatus(id, msg) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(msg || "");
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Extension install prompt ──────────────────────────────────────────────────

const _EXT_STEPS = {
  chrome: {
    name: "Chrome / Edge / Brave",
    color: "var(--accent)",
    steps: [
      { text: "Open your browser and go to <code>chrome://extensions</code>" },
      { text: "Enable <strong>Developer mode</strong> (toggle in the top-right corner)" },
      { text: "Click <strong>Load unpacked</strong>" },
      { text: "Navigate to and select the folder:<br><span class=\"path\">browser-extension/</span>" },
      { text: "The Lumi DM icon appears in your toolbar. Right-click any link → <strong>Download with Lumi DM</strong>" },
    ],
  },
  firefox: {
    name: "Firefox",
    color: "var(--orange)",
    steps: [
      { text: "Open Firefox and go to <code>about:debugging</code>" },
      { text: "Click <strong>This Firefox</strong> in the left sidebar" },
      { text: "Click <strong>Load Temporary Add-on...</strong>" },
      { text: "Navigate into the <span class=\"path\">browser-extension/</span> folder and select <span class=\"path\">manifest.json</span>" },
      { text: "The Lumi DM icon appears in Firefox. Right-click any link → <strong>Download with Lumi DM</strong>" },
      { text: "<strong>Note:</strong> Temporary add-ons are removed when Firefox closes. For a permanent install, package and submit to addons.mozilla.org." },
    ],
  },
};

function initExtBanner() {
  if (localStorage.getItem("LUMIDM.extDismissed")) return;
  const el = document.getElementById("ext-banner");
  if (el) el.style.display = "flex";
}

function dismissExtBanner() {
  localStorage.setItem("LUMIDM.extDismissed", "1");
  const el = document.getElementById("ext-banner");
  if (el) el.style.display = "none";
}

function showExtModal(browser) {
  const info = _EXT_STEPS[browser];
  if (!info) return;
  document.getElementById("ext-modal-browser-name").textContent = info.name;
  document.getElementById("ext-modal-body").innerHTML = info.steps.map((s, i) => `
    <div class="ext-step">
      <div class="ext-step-num">${i + 1}</div>
      <div class="ext-step-txt">${s.text}</div>
    </div>`).join("");
  const modal = document.getElementById("ext-modal");
  if (modal) modal.classList.add("open");
}

function closeExtModal() {
  const modal = document.getElementById("ext-modal");
  if (modal) modal.classList.remove("open");
}

// Show banner after a short delay so it doesn't flash on first paint
setTimeout(initExtBanner, 800);
