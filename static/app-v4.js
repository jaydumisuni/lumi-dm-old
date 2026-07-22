"use strict";

const state = {
  auth: null,
  bearerToken: sessionStorage.getItem("LUMI.bearerToken") || "",
  view: "overview",
  search: "",
  statusFilter: "all",
  tasks: [],
  queues: [],
  categories: [],
  hostProfiles: [],
  settings: {},
  capabilities: {},
  overview: {},
  diagnostics: null,
  inspector: null,
  inspectorTab: "overview",
  sourceTab: "direct",
  mediaInfo: null,
  torrentInfo: null,
  grabResults: [],
  busy: new Set(),
  pollTimer: null,
};

const viewMeta = {
  overview: ["Overview", "Your download activity at a glance"],
  downloads: ["All downloads", "Every source, queue and state"],
  unfinished: ["Unfinished", "Running, queued, paused and failed tasks"],
  finished: ["Finished", "Completed downloads and post-processing results"],
  queues: ["Queues", "Control order, limits and scheduled groups"],
  categories: ["Categories", "Automatic file placement and archive rules"],
  grabber: ["LinkGrabber", "Inspect pages and queue selected resources"],
  settings: ["Settings", "Storage, connections, sites and secure clients"],
  diagnostics: ["Diagnostics", "Health, recovery and privacy-safe evidence"],
};

const unfinishedStatuses = new Set([
  "staged", "queued", "resolving", "running", "pausing", "paused",
  "needs_link", "verifying", "post_processing", "failed", "cancelling",
]);
const activeStatuses = new Set([
  "queued", "resolving", "running", "pausing", "verifying",
  "post_processing", "cancelling",
]);

window.addEventListener("DOMContentLoaded", init);

async function init() {
  bindStaticEvents();
  try {
    await establishSession();
    document.getElementById("boot-screen").hidden = true;
    document.getElementById("app-shell").hidden = false;
    await refreshFoundation();
    renderCurrentView();
    startPolling();
  } catch (error) {
    showBootError(error);
  }
}

function bindStaticEvents() {
  document.getElementById("boot-retry")?.addEventListener("click", () => location.reload());
  document.querySelectorAll(".nav-item").forEach(button => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  document.getElementById("new-download-btn")?.addEventListener("click", openNewModal);
  document.getElementById("global-search")?.addEventListener("input", event => {
    state.search = event.target.value.trim().toLowerCase();
    if (["overview", "downloads", "unfinished", "finished"].includes(state.view)) {
      renderCurrentView();
    }
  });
  document.getElementById("sidebar-open")?.addEventListener("click", () => {
    document.getElementById("sidebar")?.classList.add("open");
  });
  document.getElementById("sidebar-close")?.addEventListener("click", closeSidebar);
  document.getElementById("content")?.addEventListener("click", handleContentClick);
  document.getElementById("content")?.addEventListener("submit", handleContentSubmit);
  document.getElementById("content")?.addEventListener("dblclick", event => {
    const row = event.target.closest(".download-row[data-task]");
    if (row) openInspector(row.dataset.task);
  });
  document.getElementById("content")?.addEventListener("contextmenu", event => {
    const row = event.target.closest(".download-row[data-task]");
    if (!row) return;
    event.preventDefault();
    showContextMenu(row.dataset.task, event.clientX, event.clientY);
  });
  document.getElementById("context-menu")?.addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    hideContextMenu();
    void handleTaskAction(button.dataset.action, button.dataset.task);
  });
  document.addEventListener("click", event => {
    if (!event.target.closest("#context-menu") && !event.target.closest(".row-menu")) {
      hideContextMenu();
    }
  });
  window.addEventListener("resize", hideContextMenu);
  document.querySelectorAll("[data-close-modal]").forEach(button => {
    button.addEventListener("click", () => closeModal(button.dataset.closeModal));
  });
  document.querySelectorAll(".modal-backdrop").forEach(backdrop => {
    backdrop.addEventListener("click", event => {
      if (event.target === backdrop) closeModal(backdrop.id);
    });
  });
  document.getElementById("source-tabs")?.addEventListener("click", event => {
    const button = event.target.closest("button[data-source]");
    if (!button) return;
    state.sourceTab = button.dataset.source;
    renderSourceModal();
  });
  document.getElementById("source-body")?.addEventListener("click", handleSourceClick);
  document.getElementById("source-body")?.addEventListener("submit", handleSourceSubmit);
  document.getElementById("queue-form")?.addEventListener("submit", createQueue);
  document.getElementById("category-form")?.addEventListener("submit", createCategory);
  document.getElementById("inspector-close")?.addEventListener("click", closeInspector);
  document.getElementById("drawer-backdrop")?.addEventListener("click", closeInspector);
  document.getElementById("inspector-tabs")?.addEventListener("click", event => {
    const button = event.target.closest("button[data-tab]");
    if (!button) return;
    state.inspectorTab = button.dataset.tab;
    renderInspector();
  });
  document.getElementById("inspector-body")?.addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (button && state.inspector) {
      void handleTaskAction(button.dataset.action, state.inspector.task.id);
    }
  });
  document.getElementById("inspector-body")?.addEventListener("submit", handleInspectorSubmit);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      hideContextMenu();
      closeInspector();
      document.querySelectorAll(".modal-backdrop:not([hidden])").forEach(item => closeModal(item.id));
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") {
      event.preventDefault();
      openNewModal();
    }
  });
}

async function establishSession() {
  if (state.bearerToken) {
    try {
      state.auth = await rawApi("GET", "/api/v4/security/me", null, false);
      if (state.auth.authenticated) return;
    } catch (_) {
      state.bearerToken = "";
      sessionStorage.removeItem("LUMI.bearerToken");
    }
  }
  const response = await fetch("/api/security/bootstrap", {
    credentials: "same-origin",
    headers: { "X-Lumi-Client": "web-ui-v4" },
  });
  const data = await readJson(response);
  if (response.ok) {
    state.auth = data;
    return;
  }
  if (response.status === 403) {
    await showRemotePairScreen(data.error || "This device must be paired.");
    return;
  }
  throw new Error(data.error || "Could not start a secure Lumi session");
}

function showRemotePairScreen(message) {
  return new Promise((resolve, reject) => {
    const boot = document.getElementById("boot-screen");
    boot.innerHTML = `
      <img src="/static/favicon-96.png" alt="Lumi" class="boot-logo">
      <div class="boot-title">Pair with Lumi</div>
      <div class="boot-status">${esc(message)}</div>
      <input class="input" id="remote-pair-code" maxlength="9" placeholder="ABCD-EFGH" style="width:220px;text-transform:uppercase;text-align:center;letter-spacing:.1em">
      <input class="input" id="remote-client-name" maxlength="120" placeholder="This device name" style="width:220px">
      <button class="btn primary" id="remote-pair-submit">Pair securely</button>
      <div class="boot-status" id="remote-pair-status"></div>`;
    const submit = document.getElementById("remote-pair-submit");
    submit.addEventListener("click", async () => {
      const code = document.getElementById("remote-pair-code").value.trim();
      const clientName = document.getElementById("remote-client-name").value.trim();
      const status = document.getElementById("remote-pair-status");
      if (!code) { status.textContent = "Enter the pairing code."; return; }
      submit.disabled = true;
      status.textContent = "Pairing…";
      try {
        const response = await fetch("/api/security/pair", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, client_name: clientName || "Lumi web client" }),
        });
        const data = await readJson(response);
        if (!response.ok || !data.token) throw new Error(data.error || "Pairing failed");
        state.bearerToken = data.token;
        sessionStorage.setItem("LUMI.bearerToken", data.token);
        state.auth = await rawApi("GET", "/api/v4/security/me", null, false);
        resolve();
      } catch (error) {
        status.textContent = error.message;
        submit.disabled = false;
      }
    });
  });
}

function showBootError(error) {
  document.getElementById("boot-status").textContent = error.message || String(error);
  document.getElementById("boot-retry").hidden = false;
}

async function rawApi(method, path, body = null, retry = true) {
  const headers = { "X-Lumi-Client": "web-ui-v4" };
  if (body !== null) headers["Content-Type"] = "application/json";
  if (state.bearerToken) headers.Authorization = `Bearer ${state.bearerToken}`;
  const response = await fetch(path, {
    method,
    credentials: state.bearerToken ? "omit" : "same-origin",
    headers,
    ...(body !== null ? { body: JSON.stringify(body) } : {}),
  });
  if (response.status === 401 && retry && !state.bearerToken) {
    await establishSession();
    return rawApi(method, path, body, false);
  }
  const data = await readJson(response);
  if (!response.ok) throw new Error(data.error || `${method} ${path} failed (${response.status})`);
  return data;
}

const api = rawApi;

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try { return JSON.parse(text); }
  catch { return { error: text.slice(0, 500) }; }
}

async function refreshFoundation() {
  const [tasks, overview, queues, categories, settings, capabilities, hostProfiles, auth] = await Promise.all([
    api("GET", "/api/downloads?limit=5000"),
    api("GET", "/api/v4/overview"),
    api("GET", "/api/queues"),
    api("GET", "/api/categories"),
    api("GET", "/api/settings"),
    api("GET", "/api/capabilities"),
    api("GET", "/api/host-profiles"),
    api("GET", "/api/v4/security/me"),
  ]);
  state.tasks = tasks.downloads || [];
  state.overview = overview || {};
  state.queues = queues.queues || [];
  state.categories = categories.categories || [];
  state.settings = settings || {};
  state.capabilities = capabilities || {};
  state.hostProfiles = hostProfiles.profiles || [];
  state.auth = auth || state.auth;
  updateChrome();
}

function startPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const [tasks, overview] = await Promise.all([
        api("GET", "/api/downloads?limit=5000"),
        api("GET", "/api/v4/overview"),
      ]);
      state.tasks = tasks.downloads || [];
      state.overview = overview || {};
      updateChrome();
      if (["overview", "downloads", "unfinished", "finished", "queues"].includes(state.view)) {
        renderCurrentView();
      }
      if (state.inspector) await refreshInspector(false);
    } catch (error) {
      setConnection(false, error.message);
    }
  }, 2200);
}

function updateChrome() {
  const unfinished = state.tasks.filter(task => unfinishedStatuses.has(task.status)).length;
  const finished = state.tasks.filter(task => task.status === "completed").length;
  text("nav-all-count", state.tasks.length);
  text("nav-unfinished-count", unfinished);
  text("nav-finished-count", finished);
  const speed = Number(state.overview.total_speed_bytes_per_sec || 0);
  text("sidebar-speed", fmtRate(speed));
  text("top-speed", fmtRate(speed));
  document.getElementById("speed-dot")?.classList.toggle("active", speed > 0);
  setConnection(true);
}

function setConnection(online, message = "") {
  const dot = document.getElementById("server-dot");
  dot?.classList.toggle("online", online);
  dot?.classList.toggle("offline", !online);
  text("server-state", online ? "Lumi ready" : "Disconnected");
  text("client-role", online ? `${state.auth?.client_name || "Client"} · ${state.auth?.role || "owner"}` : message || "Server unavailable");
}

function switchView(view) {
  if (!viewMeta[view]) return;
  state.view = view;
  closeSidebar();
  document.querySelectorAll(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view").forEach(section => section.classList.toggle("active", section.id === `view-${view}`));
  const [title, subtitle] = viewMeta[view];
  text("view-title", title);
  text("view-subtitle", subtitle);
  renderCurrentView();
  document.getElementById(`view-${view}`)?.classList.add("view-enter");
  setTimeout(() => document.getElementById(`view-${view}`)?.classList.remove("view-enter"), 220);
}

function closeSidebar() {
  document.getElementById("sidebar")?.classList.remove("open");
}

function renderCurrentView() {
  switch (state.view) {
    case "overview": return renderOverview();
    case "downloads": return renderDownloads("all");
    case "unfinished": return renderDownloads("unfinished");
    case "finished": return renderDownloads("finished");
    case "queues": return renderQueues();
    case "categories": return renderCategories();
    case "grabber": return renderGrabber();
    case "settings": return renderSettings();
    case "diagnostics": return void renderDiagnostics();
  }
}

function searched(tasks) {
  if (!state.search) return tasks;
  return tasks.filter(task => [
    task.filename, task.url, task.type, task.status, task.category_id,
    task.queue_id, task.error, task.metadata?.title,
  ].some(value => String(value || "").toLowerCase().includes(state.search)));
}

function renderOverview() {
  const element = document.getElementById("view-overview");
  const counts = state.overview.counts || {};
  const active = state.tasks.filter(task => activeStatuses.has(task.status));
  const recent = searched(state.tasks).slice(0, 8);
  const warnings = Number(state.overview.warnings || 0);
  element.innerHTML = `
    <div class="stats-grid">
      ${statCard("Active", active.length, `${fmtRate(state.overview.total_speed_bytes_per_sec || 0)} combined`, active.length ? "good" : "")}
      ${statCard("Completed", counts.completed || 0, `${fmtBytes(state.overview.downloaded_bytes || 0)} transferred`, "good")}
      ${statCard("Waiting", (counts.queued || 0) + (counts.paused || 0) + (counts.needs_link || 0), "Queued, paused or awaiting a link", "")}
      ${statCard("Warnings", warnings + (counts.failed || 0), warnings ? "Completed tasks need attention" : "Failures and recovery notices", warnings ? "warn" : "")}
    </div>
    <div class="two-column">
      <section class="panel"><div class="panel-head"><div><h2>Recent downloads</h2><p>Double-click a task to inspect it</p></div><button class="text-btn" data-action="view-all">View all</button></div><div>${recent.length ? taskTable(recent, true) : emptyState("No downloads yet", "Add your first URL, media source or torrent.")}</div></section>
      <section class="panel"><div class="panel-head"><div><h2>Current activity</h2><p>Live queue and transfer state</p></div></div><div class="panel-body">${active.length ? `<div class="bar-list">${active.slice(0, 7).map(miniTask).join("")}</div>` : emptyState("Nothing active", "Queued work will appear here.")}${systemSummary()}</div></section>
    </div>`;
}

function statCard(label, value, note, className = "") {
  return `<article class="stat-card ${className}"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-note">${esc(note)}</div></article>`;
}

function miniTask(task) {
  return `<div class="mini-task"><div><div class="mini-task-name" title="${esc(task.filename)}">${esc(task.filename || task.url || task.id)}</div><div class="mini-task-meta">${esc(statusLabel(task.status))} · ${fmtRate(task.speed_bytes_per_sec || 0)}</div><div class="progress-track" style="margin-top:6px"><div class="progress-fill ${esc(task.status)}" style="width:${progress(task)}%"></div></div></div><div class="mini-progress">${progress(task).toFixed(1)}%</div></div>`;
}

function systemSummary() {
  const database = state.overview.database || {};
  return `<div class="summary-list" style="margin-top:17px"><div class="summary-row"><span>Database</span><strong>${database.ok ? "Healthy" : "Needs attention"}</strong></div><div class="summary-row"><span>Named queues</span><strong>${state.queues.length}</strong></div><div class="summary-row"><span>Categories</span><strong>${state.categories.length}</strong></div><div class="summary-row"><span>Session role</span><strong>${esc(state.auth?.role || "owner")}</strong></div></div>`;
}

function renderDownloads(kind) {
  const id = kind === "all" ? "downloads" : kind;
  const element = document.getElementById(`view-${id}`);
  let tasks = searched(state.tasks);
  if (kind === "unfinished") tasks = tasks.filter(task => unfinishedStatuses.has(task.status));
  if (kind === "finished") tasks = tasks.filter(task => task.status === "completed");
  if (state.statusFilter !== "all") tasks = tasks.filter(task => task.status === state.statusFilter);
  const statuses = ["all", "running", "queued", "paused", "needs_link", "post_processing", "failed", "completed"];
  element.innerHTML = `
    <div class="page-tools">
      <div class="segmented">${statuses.map(status => `<button class="${state.statusFilter === status ? "active" : ""}" data-action="status-filter" data-status="${status}">${esc(status === "all" ? "All" : statusLabel(status))}</button>`).join("")}</div>
      <span class="spacer"></span>
      <button class="btn" data-action="pause-all">Ⅱ Pause all</button>
      <button class="btn" data-action="resume-all">▶ Resume all</button>
      <button class="btn" data-action="refresh">↻ Refresh</button>
      <button class="btn danger" data-action="clear-done">Clear done</button>
    </div>
    ${tasks.length ? taskTable(tasks) : emptyState("No matching downloads", "Change the filter or add a new source.")}`;
}

function taskTable(tasks, compact = false) {
  return `<div class="download-table"><div class="table-head"><span>File</span><span>Status</span><span class="size-cell">Size</span><span>Speed</span><span></span></div>${tasks.map(task => taskRow(task, compact)).join("")}</div>`;
}

function taskRow(task, compact = false) {
  const pct = progress(task);
  const warning = task.metadata?.completion_warning || task.post_process?.warning;
  const speed = Number(task.speed_bytes_per_sec || 0);
  const eta = speed > 0 && task.total_bytes > task.downloaded_bytes ? fmtDuration((task.total_bytes - task.downloaded_bytes) / speed) : "";
  return `<article class="download-row ${compact ? "compact" : ""}" data-task="${esc(task.id)}">
    <div class="download-name"><div class="file-icon">${fileGlyph(task)}</div><div class="file-main"><div class="file-title" title="${esc(task.filename || task.url)}">${esc(task.filename || task.url || task.id)}</div><div class="file-meta"><span>${esc(task.type || "download")}</span><span>·</span><span>${esc(task.category_id || "other")}</span><span>·</span><span>${esc(task.queue_id || "default")}</span>${warning ? `<span class="warning-dot" title="${esc(warning)}"></span>` : ""}</div></div></div>
    <div class="status-cell"><div class="status-line"><span class="status-pill ${esc(task.status)}">${esc(statusLabel(task.status))}</span><span>${pct.toFixed(1)}%</span></div><div class="progress-track"><div class="progress-fill ${esc(task.status)}" style="width:${pct}%"></div></div></div>
    <div class="size-cell"><div class="cell-title">${fmtBytes(task.downloaded_bytes || 0)}</div><div class="cell-sub">of ${fmtBytes(task.total_bytes || 0)}</div></div>
    <div class="speed-cell"><div class="cell-title">${speed ? fmtRate(speed) : eta || "—"}</div><div class="cell-sub">${speed && eta ? `${eta} left` : esc(task.mode || task.metadata?.torrent_state || "")}</div></div>
    <button class="icon-btn row-menu" data-action="task-menu" data-task="${esc(task.id)}" aria-label="Task menu">⋯</button>
  </article>`;
}

function fileGlyph(task) {
  const type = String(task.type || "").toLowerCase();
  const ext = String(task.filename || "").split(".").pop().toLowerCase();
  if (type === "video" || ["mp4","mkv","webm","mov"].includes(ext)) return "▶";
  if (type === "torrent") return "⇅";
  if (["zip","rar","7z","gz","tar"].includes(ext) || type === "archive") return "▣";
  if (["mp3","flac","wav","m4a","opus"].includes(ext)) return "♪";
  if (["pdf","doc","docx","txt","epub"].includes(ext)) return "▤";
  if (["exe","msi","apk","dmg","pkg"].includes(ext)) return "◆";
  return "↓";
}

function renderQueues() {
  const element = document.getElementById("view-queues");
  const counts = {};
  state.tasks.forEach(task => {
    const key = task.queue_id || "default";
    counts[key] ||= { total: 0, running: 0, queued: 0 };
    counts[key].total++;
    if (activeStatuses.has(task.status)) counts[key].running++;
    if (task.status === "queued") counts[key].queued++;
  });
  element.innerHTML = `<div class="page-tools"><span class="spacer"></span><button class="btn primary" data-action="open-queue-modal">＋ Create queue</button></div><div class="queue-grid">${state.queues.map(queue => {
    const current = counts[queue.id] || { total: 0, running: 0, queued: 0 };
    return `<article class="entity-card ${queue.active ? "" : "inactive"}"><div class="entity-head"><div><h3>${esc(queue.name)}</h3><p>${esc(queue.id)}</p></div><div class="entity-actions"><button class="chip-btn" data-action="toggle-queue" data-id="${esc(queue.id)}" data-active="${queue.active}">${queue.active ? "Pause" : "Start"}</button>${queue.id !== "default" ? `<button class="chip-btn" data-action="delete-queue" data-id="${esc(queue.id)}">Delete</button>` : ""}</div></div><div class="entity-stats"><div class="entity-stat"><span>Tasks</span><strong>${current.total}</strong></div><div class="entity-stat"><span>Running</span><strong>${current.running}</strong></div><div class="entity-stat"><span>Waiting</span><strong>${current.queued}</strong></div></div><div class="tag-list"><span class="tag">Limit ${queue.max_running || "global"}</span>${queue.speed_limit_bps ? `<span class="tag">${fmtRate(queue.speed_limit_bps)}</span>` : ""}${queue.stop_when_empty ? `<span class="tag">Stop when empty</span>` : ""}</div></article>`;
  }).join("")}</div>`;
}

function renderCategories() {
  const element = document.getElementById("view-categories");
  const counts = {};
  state.tasks.forEach(task => counts[task.category_id || "other"] = (counts[task.category_id || "other"] || 0) + 1);
  element.innerHTML = `<div class="page-tools"><span class="spacer"></span><button class="btn primary" data-action="open-category-modal">＋ Create category</button></div><div class="category-grid">${state.categories.map(category => `<article class="entity-card ${category.enabled === false ? "inactive" : ""}"><div class="entity-head"><div><h3>${esc(category.name)}</h3><p>${esc(category.folder || "No subfolder")}</p></div><div class="entity-actions">${category.id !== "other" ? `<button class="chip-btn" data-action="delete-category" data-id="${esc(category.id)}">Delete</button>` : ""}</div></div><div class="entity-stats"><div class="entity-stat"><span>Tasks</span><strong>${counts[category.id] || 0}</strong></div><div class="entity-stat"><span>Extensions</span><strong>${(category.extensions || []).length}</strong></div><div class="entity-stat"><span>Domains</span><strong>${(category.domains || []).length}</strong></div></div><div class="tag-list">${(category.extensions || []).slice(0, 8).map(item => `<span class="tag">.${esc(item)}</span>`).join("")}${category.auto_extract ? `<span class="tag">Auto extract</span>` : ""}</div></article>`).join("")}</div>`;
}

function renderGrabber() {
  const element = document.getElementById("view-grabber");
  element.innerHTML = `<div class="grabber-layout"><section class="form-card"><form class="form-stack" data-form="grabber"><label>Page URL<input class="input" name="url" type="url" required placeholder="https://example.com/downloads"></label><div class="field-row"><label>Maximum pages<input class="input" name="max_pages" type="number" min="1" max="50" value="10"></label><label>Mode<select class="select" name="mode"><option value="single">Scan this page</option><option value="crawl">Crawl linked pages</option></select></label></div><label class="check"><input name="include_videos" type="checkbox" checked>Include detected media</label><label class="check"><input name="include_files" type="checkbox" checked>Include file links</label><button class="btn primary" type="submit">⌁ Scan links</button></form></section><section class="panel"><div class="panel-head"><div><h2>Detected resources</h2><p>${state.grabResults.length} result${state.grabResults.length === 1 ? "" : "s"}</p></div>${state.grabResults.length ? `<button class="btn primary" data-action="queue-grabbed">Queue selected</button>` : ""}</div><div class="panel-body">${state.grabResults.length ? `<div class="result-list">${state.grabResults.map((item,index) => `<label class="result-row"><input type="checkbox" data-grab-index="${index}" checked><div><div class="name">${esc(item.filename || item.title || fileNameFromUrl(item.url))}</div><div class="url" title="${esc(item.url)}">${esc(item.url)}</div></div><span class="tag">${esc(item.type || item.ext || "file")}</span><span>${item.size ? fmtBytes(item.size) : "—"}</span></label>`).join("")}</div>` : emptyState("Nothing scanned yet", "Enter a page URL and run LinkGrabber.")}</div></section></div>`;
}

function renderSettings() {
  const element = document.getElementById("view-settings");
  element.innerHTML = `<div class="settings-layout"><nav class="settings-nav"><button class="active" data-action="settings-tab" data-tab="general">General</button><button data-action="settings-tab" data-tab="storage">Storage</button><button data-action="settings-tab" data-tab="sites">Sites & sessions</button><button data-action="settings-tab" data-tab="security">Security</button><button data-action="settings-tab" data-tab="engines">Engines</button></nav><div>
    <section class="settings-section active" data-settings-section="general">${generalSettingsHtml()}</section>
    <section class="settings-section" data-settings-section="storage">${storageSettingsHtml()}</section>
    <section class="settings-section" data-settings-section="sites">${hostProfilesHtml()}</section>
    <section class="settings-section" data-settings-section="security">${securitySettingsHtml()}</section>
    <section class="settings-section" data-settings-section="engines">${engineSettingsHtml()}</section>
  </div></div>`;
}

function generalSettingsHtml() {
  return `<section class="settings-card"><div class="settings-card-head"><h3>Transfer limits</h3><p>Global task and connection behaviour</p></div><div class="settings-card-body"><form data-form="general-settings"><div class="setting-row"><div class="setting-label"><strong>Maximum simultaneous downloads</strong><small>Shared by every queue unless overridden</small></div><input class="input" name="max_concurrent" type="number" min="1" max="128" value="${esc(state.settings.max_concurrent || state.capabilities.max_concurrent || 8)}"></div><div class="setting-row"><div class="setting-label"><strong>Default connections per download</strong><small>Adaptive HTTP grows only when useful</small></div><input class="input" name="default_connections" type="number" min="1" max="128" value="${esc(state.settings.default_connections || 8)}"></div><div class="setting-row"><div class="setting-label"><strong>When all work finishes</strong><small>Runs only after post-processing also ends</small></div><select class="select" name="completion_action">${["none","sleep","shutdown","restart"].map(item => `<option value="${item}" ${state.settings.completion_action === item ? "selected" : ""}>${statusLabel(item)}</option>`).join("")}</select></div><div class="form-actions"><button class="btn primary" type="submit">Save transfer settings</button></div></form></div></section>`;
}

function storageSettingsHtml() {
  return `<section class="settings-card"><div class="settings-card-head"><h3>Download locations</h3><p>Temporary assembly and final library remain separate</p></div><div class="settings-card-body"><form data-form="storage-settings"><div class="setting-row"><div class="setting-label"><strong>Default final folder</strong><small>Category folders are created below this location</small></div><input class="input" name="default_dir" value="${esc(state.settings.default_dir || "")}"></div><div class="setting-row"><div class="setting-label"><strong>Temporary download folder</strong><small>Segments and partial files stay here until verified</small></div><input class="input" name="temp_dir" value="${esc(state.settings.temp_dir || "")}"></div><div class="form-actions"><button class="btn primary" type="submit">Save locations</button></div></form></div></section>`;
}

function hostProfilesHtml() {
  return `<section class="settings-card"><div class="settings-card-head"><h3>Per-site profiles</h3><p>Connection, speed, proxy and browser interception rules</p></div><div class="settings-card-body"><form class="form-stack" data-form="host-profile"><div class="field-row"><label>Profile name<input class="input" name="name" required></label><label>Host pattern<input class="input" name="host_pattern" required placeholder="downloads.example.com"></label></div><div class="field-row"><label>Max connections<input class="input" name="max_connections" type="number" min="0" max="128" value="0"></label><label>Speed limit (bytes/s)<input class="input" name="speed_limit_bps" type="number" min="0" value="0"></label></div><div class="field-row"><label>Interception<select class="select" name="intercept_mode"><option value="auto">Automatic</option><option value="always_lumi">Always Lumi</option><option value="always_browser">Always browser</option></select></label><label>Proxy URL<input class="input" name="proxy_url" placeholder="http://proxy:8080"></label></div><div class="field-row"><label>Username<input class="input" name="username" autocomplete="off"></label><label>Password<input class="input" name="password" type="password" autocomplete="new-password"></label></div><button class="btn primary" type="submit">Save site profile</button></form><div style="margin-top:16px">${state.hostProfiles.length ? state.hostProfiles.map(profile => `<div class="client-row"><div><strong>${esc(profile.name)}</strong><small>${esc(profile.host_pattern)} · ${esc(profile.intercept_mode)}</small></div><span class="tag">${profile.max_connections || "adaptive"}</span><button class="icon-btn" data-action="delete-host-profile" data-id="${esc(profile.id)}">×</button></div>`).join("") : `<div class="empty">No site-specific profiles.</div>`}</div></div></section>`;
}

function securitySettingsHtml() {
  return `<section class="settings-card"><div class="settings-card-head"><h3>Pair a browser or another device</h3><p>Codes expire after ten minutes and can grant owner or read-only access</p></div><div class="settings-card-body"><form class="form-stack" data-form="pairing-code"><div class="field-row"><label>Client name<input class="input" name="client_name" required value="Browser extension"></label><label>Role<select class="select" name="role"><option value="owner">Owner</option><option value="read_only">Read only</option></select></label></div><button class="btn primary" type="submit">Generate one-time code</button><div id="pair-code-output"></div></form></div></section><section class="settings-card"><div class="settings-card-head"><h3>Paired clients</h3><p>Revoke a token immediately when a device is no longer trusted</p></div><div class="settings-card-body" id="paired-clients"><div class="empty">Loading secure clients…</div></div></section>`;
}

function engineSettingsHtml() {
  const rows = [
    ["Adaptive HTTP", state.capabilities.http], ["FTP", state.capabilities.ftp],
    ["Video / yt-dlp", state.capabilities.video || state.capabilities.media_v3],
    ["Torrent", state.capabilities.torrent], ["7-Zip", state.capabilities.archive_7zip],
    ["Secure extraction", state.capabilities.archive_secure_extract],
    ["Post-processing", state.capabilities.post_processing],
  ];
  return `<section class="settings-card"><div class="settings-card-head"><h3>Runtime engines</h3><p>Detected from the source runtime, not the packaging system</p></div><div class="settings-card-body"><div class="summary-list">${rows.map(([name,on]) => `<div class="summary-row"><span>${esc(name)}</span><strong style="color:${on ? "var(--green)" : "var(--red)"}">${on ? "Available" : "Not detected"}</strong></div>`).join("")}</div></div></section>`;
}

async function renderDiagnostics(force = false) {
  const element = document.getElementById("view-diagnostics");
  if (force || !state.diagnostics) {
    element.innerHTML = `<div class="empty">Running health checks…</div>`;
    try { state.diagnostics = await api("GET", "/api/v4/diagnostics"); }
    catch (error) { element.innerHTML = emptyState("Diagnostics failed", error.message); return; }
  }
  const data = state.diagnostics;
  const database = data.database || {};
  const storage = data.storage || {};
  const missing = data.missing_files || {};
  element.innerHTML = `<div class="health-grid">${healthCard("Database", database.ok, database.ok ? "Integrity and foreign keys are healthy" : "Database needs maintenance", "▤")}${healthCard("Storage", storage.ok, storage.ok ? "Download locations are writable" : "One or more locations need attention", "◫")}${healthCard("Completed files", !missing.missing_count, missing.missing_count ? `${missing.missing_count} recorded file(s) are missing` : "Recorded completed files are present", "✓")}</div><div class="two-column"><section class="panel"><div class="panel-head"><div><h2>Maintenance actions</h2><p>Every destructive repair creates a backup first</p></div></div><div class="panel-body"><div class="page-tools"><button class="btn" data-action="diagnostic-export">Export diagnostics</button><button class="btn" data-action="database-backup">Backup database</button><button class="btn" data-action="database-repair">Repair database</button><button class="btn" data-action="recovery-export">Recovery export</button><button class="btn" data-action="scan-missing">Scan missing files</button><button class="btn" data-action="refresh-diagnostics">↻ Refresh</button></div><div id="diagnostic-result" class="diagnostic-output">${esc(JSON.stringify(data, null, 2))}</div></div></section><section class="panel"><div class="panel-head"><div><h2>Runtime</h2><p>Source application evidence</p></div></div><div class="panel-body summary-list">${Object.entries(data.application || {}).map(([key,value]) => `<div class="summary-row"><span>${esc(humanize(key))}</span><strong>${esc(value)}</strong></div>`).join("")}${Object.entries(data.engines || {}).map(([key,value]) => `<div class="summary-row"><span>${esc(humanize(key))}</span><strong>${value ? "Available" : "Not detected"}</strong></div>`).join("")}</div></section></div>`;
}

function healthCard(title, ok, note, icon) {
  return `<article class="health-card ${ok ? "ok" : "bad"}"><div class="health-icon">${icon}</div><h3>${esc(title)} · ${ok ? "Healthy" : "Attention"}</h3><p>${esc(note)}</p></article>`;
}

function openNewModal() {
  state.sourceTab = "direct";
  state.mediaInfo = null;
  state.torrentInfo = null;
  document.getElementById("new-modal").hidden = false;
  renderSourceModal();
}

function renderSourceModal() {
  document.querySelectorAll("#source-tabs button").forEach(button => button.classList.toggle("active", button.dataset.source === state.sourceTab));
  const body = document.getElementById("source-body");
  if (state.sourceTab === "direct") body.innerHTML = directSourceHtml();
  if (state.sourceTab === "video") body.innerHTML = videoSourceHtml();
  if (state.sourceTab === "torrent") body.innerHTML = torrentSourceHtml();
  if (state.sourceTab === "archive") body.innerHTML = archiveSourceHtml();
}

function commonDestinationFields() {
  return `<div class="field-row"><label class="field">Final folder<input class="input" name="target_dir" value="${esc(state.settings.default_dir || "")}"></label><label class="field">Queue<select class="select" name="queue_id">${state.queues.map(queue => `<option value="${esc(queue.id)}">${esc(queue.name)}</option>`).join("")}</select></label></div>`;
}

function directSourceHtml() {
  return `<form class="source-options" data-source-form="direct"><label class="field">URLs<textarea class="textarea" name="urls" required placeholder="One HTTP, HTTPS or FTP URL per line"></textarea></label>${commonDestinationFields()}<div class="field-row"><label class="field">Filename<input class="input" name="filename" placeholder="Optional for one URL"></label><label class="field">Category<select class="select" name="category_id"><option value="">Automatic</option>${state.categories.map(category => `<option value="${esc(category.id)}">${esc(category.name)}</option>`).join("")}</select></label></div><div class="field-row"><label class="field">Connections<input class="input" name="connections" type="number" min="1" max="128" value="${esc(state.settings.default_connections || 8)}"></label><label class="field">Duplicate handling<select class="select" name="duplicate_policy"><option value="reuse">Reuse existing task</option><option value="rename">Create numbered filename</option><option value="overwrite">Overwrite file</option><option value="reject">Reject duplicate</option></select></label></div><label class="check"><input type="checkbox" name="start_paused">Add paused</label><div class="form-actions"><button class="btn" type="button" data-close-source>Cancel</button><button class="btn primary" type="submit">Queue download</button></div></form>`;
}

function videoSourceHtml() {
  const info = state.mediaInfo;
  return `<div class="source-options"><form class="field-row" data-source-form="media-inspect"><label class="field" style="grid-column:1/-1">Video or playlist URL<input class="input" name="url" type="url" required value="${esc(info?.webpage_url || "")}" placeholder="https://youtube.com/watch?v=…"></label><button class="btn" type="submit">Inspect formats</button></form>${info ? `<form class="source-options" data-source-form="video-start"><input type="hidden" name="url" value="${esc(info.webpage_url)}"><div class="panel"><div class="panel-head"><div><h2>${esc(info.title)}</h2><p>${info.entries?.length ? `${info.entries.length} playlist entries` : fmtDuration(info.duration || 0)}</p></div></div><div class="panel-body"><label class="field">Format<select class="select" name="format_id">${(info.formats || []).map(format => `<option value="${esc(format.format_id)}">${esc(formatLabel(format))}</option>`).join("")}</select></label>${info.entries?.length ? `<div class="file-select-list" style="margin-top:10px">${info.entries.map(entry => `<label class="option-row"><input type="checkbox" name="playlist_item" value="${entry.index}" checked><div><strong>${esc(entry.title)}</strong><small>${fmtDuration(entry.duration || 0)}</small></div><span>${entry.index}</span></label>`).join("")}</div>` : ""}</div></div>${commonDestinationFields()}<div class="field-row"><label class="check"><input type="checkbox" name="audio_only">Audio only</label><label class="check"><input type="checkbox" name="video_only">Video only</label><label class="check"><input type="checkbox" name="subtitles">Subtitles</label><label class="check"><input type="checkbox" name="thumbnail" checked>Thumbnail</label></div><div class="form-actions"><button class="btn primary" type="submit">Queue media</button></div></form>` : `<div class="empty">Inspect the source to choose its real formats, playlist entries and captions.</div>`}</div>`;
}

function torrentSourceHtml() {
  const info = state.torrentInfo;
  return `<div class="source-options"><form class="field-row" data-source-form="torrent-inspect"><label class="field" style="grid-column:1/-1">Magnet link or .torrent source<input class="input" name="source" required value="${esc(info?.source || "")}" placeholder="magnet:?xt=urn:btih:…"></label><button class="btn" type="submit">Inspect torrent</button></form>${info ? `<form class="source-options" data-source-form="torrent-start"><input type="hidden" name="url" value="${esc(info.source)}"><div class="panel"><div class="panel-head"><div><h2>${esc(info.name)}</h2><p>${info.metadata_pending ? "Metadata will resolve after starting" : `${(info.files || []).length} files · ${fmtBytes(info.total_bytes || 0)}`}</p></div></div>${info.files?.length ? `<div class="panel-body file-select-list">${info.files.map(file => `<label class="option-row"><input type="checkbox" name="torrent_file" value="${file.index}" checked><div><strong>${esc(file.path)}</strong><small>${fmtBytes(file.size)}</small></div><select class="select" name="priority_${file.index}"><option value="1">Normal</option><option value="7">High</option><option value="0">Skip</option></select></label>`).join("")}</div>` : ""}</div>${commonDestinationFields()}<div class="field-row"><label class="field">Seed ratio<input class="input" name="seed_ratio" type="number" min="0" step="0.1" value="0"></label><label class="field">Seed time (seconds)<input class="input" name="seed_time_seconds" type="number" min="0" value="0"></label></div><label class="check"><input type="checkbox" name="stop_after_download" checked>Stop immediately after download</label><div class="form-actions"><button class="btn primary" type="submit">Queue torrent</button></div></form>` : `<div class="empty">Inspect a torrent file or magnet link to choose files and priorities.</div>`}</div>`;
}

function archiveSourceHtml() {
  return `<form class="source-options" data-source-form="archive"><label class="field">Archive path<input class="input" name="path" required placeholder="C:\\Downloads\\archive.part1.rar"></label><label class="field">Destination folder<input class="input" name="destination_root" placeholder="Leave blank to extract beside archive"></label><label class="field">Password<input class="input" name="password" type="password" autocomplete="off"></label><div class="field-row"><button class="btn" type="button" data-action="inspect-archive">Inspect contents</button><button class="btn" type="button" data-action="test-archive">Test integrity</button></div><label class="check"><input type="checkbox" name="delete_archive">Delete archive parts after successful extraction</label><div id="archive-result" class="diagnostic-output" hidden></div><div class="form-actions"><button class="btn primary" type="submit">Extract securely</button></div></form>`;
}

async function handleContentClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "view-all") return switchView("downloads");
  if (action === "task-menu") return showContextMenu(button.dataset.task, button.getBoundingClientRect().right, button.getBoundingClientRect().bottom);
  if (action === "status-filter") { state.statusFilter = button.dataset.status; return renderCurrentView(); }
  if (action === "refresh") { await refreshFoundation(); return renderCurrentView(); }
  if (action === "pause-all") return bulkAction("pause-all");
  if (action === "resume-all") return bulkAction("resume-all");
  if (action === "clear-done") return bulkAction("clear");
  if (action === "open-queue-modal") return openModal("queue-modal");
  if (action === "open-category-modal") return openModal("category-modal");
  if (action === "toggle-queue") return toggleQueue(button.dataset.id, button.dataset.active !== "true");
  if (action === "delete-queue") return deleteQueue(button.dataset.id);
  if (action === "delete-category") return deleteCategory(button.dataset.id);
  if (action === "queue-grabbed") return queueGrabbed();
  if (action === "settings-tab") return switchSettingsTab(button.dataset.tab);
  if (action === "delete-host-profile") return deleteHostProfile(button.dataset.id);
  if (action === "revoke-client") return revokeClient(button.dataset.id);
  if (action === "diagnostic-export") return diagnosticAction("export");
  if (action === "database-backup") return diagnosticAction("backup");
  if (action === "database-repair") return diagnosticAction("repair");
  if (action === "recovery-export") return diagnosticAction("recovery");
  if (action === "scan-missing") return diagnosticAction("missing");
  if (action === "refresh-diagnostics") { state.diagnostics = null; return renderDiagnostics(true); }
}

async function handleContentSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const kind = form.dataset.form;
  if (kind === "grabber") return scanGrabber(form);
  if (kind === "general-settings") return saveGeneralSettings(form);
  if (kind === "storage-settings") return saveStorageSettings(form);
  if (kind === "host-profile") return saveHostProfile(form);
  if (kind === "pairing-code") return generatePairingCode(form);
}

async function handleSourceClick(event) {
  if (event.target.closest("[data-close-source]")) return closeModal("new-modal");
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "inspect-archive") return archiveAction("inspect");
  if (action === "test-archive") return archiveAction("test");
}

async function handleSourceSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const kind = form.dataset.sourceForm;
  if (kind === "direct") return startDirect(form);
  if (kind === "media-inspect") return inspectMedia(form);
  if (kind === "video-start") return startMedia(form);
  if (kind === "torrent-inspect") return inspectTorrent(form);
  if (kind === "torrent-start") return startTorrent(form);
  if (kind === "archive") return extractArchive(form);
}

async function createQueue(event) {
  event.preventDefault();
  const data = formObject(event.target);
  try {
    await api("POST", "/api/queues", { name: data.name, id: data.id, max_running: Number(data.max_running || 0), active: true });
    closeModal("queue-modal");
    event.target.reset();
    await refreshFoundation();
    renderQueues();
    toast("Queue created", data.name, "success");
  } catch (error) { toast("Queue not created", error.message, "error"); }
}

async function createCategory(event) {
  event.preventDefault();
  const data = formObject(event.target);
  try {
    await api("POST", "/api/categories", {
      id: data.id, name: data.name, folder: data.folder,
      extensions: splitList(data.extensions), domains: splitList(data.domains),
      auto_extract: Boolean(data.auto_extract), enabled: true,
    });
    closeModal("category-modal");
    event.target.reset();
    await refreshFoundation();
    renderCategories();
    toast("Category created", data.name, "success");
  } catch (error) { toast("Category not created", error.message, "error"); }
}

async function startDirect(form) {
  const data = formObject(form);
  const urls = String(data.urls || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean);
  if (!urls.length) return;
  setBusy(form, true);
  const errors = [];
  let started = 0;
  for (const url of urls) {
    try {
      await api("POST", "/api/downloads/start", {
        url,
        target_dir: data.target_dir,
        filename: urls.length === 1 ? data.filename : "",
        queue_id: data.queue_id || "default",
        category_id: data.category_id || "",
        connections: Number(data.connections || 8),
        duplicate_policy: data.duplicate_policy || "reuse",
        overwrite: data.duplicate_policy === "overwrite",
        start_paused: Boolean(data.start_paused),
      });
      started++;
    } catch (error) { errors.push(`${url}: ${error.message}`); }
  }
  setBusy(form, false);
  if (started) {
    closeModal("new-modal");
    await refreshFoundation();
    switchView("downloads");
    toast("Download queued", `${started} task${started === 1 ? "" : "s"} added`, "success");
  }
  if (errors.length) toast("Some URLs failed", errors.join(" · "), "error");
}

async function inspectMedia(form) {
  const url = new FormData(form).get("url");
  setBusy(form, true);
  try {
    state.mediaInfo = await api("GET", `/api/v3/media/info?url=${encodeURIComponent(url)}&playlist=true`);
    renderSourceModal();
  } catch (error) { toast("Media inspection failed", error.message, "error"); }
  finally { setBusy(form, false); }
}

async function startMedia(form) {
  const data = formObject(form);
  const playlistItems = [...form.querySelectorAll("input[name=playlist_item]:checked")].map(item => Number(item.value));
  try {
    await api("POST", "/api/v3/media/start", {
      url: data.url, target_dir: data.target_dir, queue_id: data.queue_id,
      format_id: data.format_id, playlist: Boolean(state.mediaInfo?.entries?.length),
      playlist_items: playlistItems, audio_only: Boolean(data.audio_only),
      video_only: Boolean(data.video_only), subtitles: Boolean(data.subtitles),
      thumbnail: Boolean(data.thumbnail), metadata: true,
    });
    closeModal("new-modal");
    await refreshFoundation();
    switchView("downloads");
    toast("Media queued", state.mediaInfo?.title || data.url, "success");
  } catch (error) { toast("Media not queued", error.message, "error"); }
}

async function inspectTorrent(form) {
  const source = new FormData(form).get("source");
  setBusy(form, true);
  try {
    state.torrentInfo = await api("GET", `/api/v3/torrent/info?source=${encodeURIComponent(source)}`);
    renderSourceModal();
  } catch (error) { toast("Torrent inspection failed", error.message, "error"); }
  finally { setBusy(form, false); }
}

async function startTorrent(form) {
  const data = formObject(form);
  const selected = [...form.querySelectorAll("input[name=torrent_file]:checked")].map(item => Number(item.value));
  const priorities = (state.torrentInfo?.files || []).map(file => {
    const select = form.querySelector(`[name="priority_${file.index}"]`);
    if (!selected.includes(file.index)) return 0;
    return Number(select?.value || 1);
  });
  try {
    await api("POST", "/api/v3/torrent/start", {
      url: data.url, target_dir: data.target_dir, queue_id: data.queue_id,
      selected_files: selected, file_priorities: priorities,
      seed_ratio: Number(data.seed_ratio || 0),
      seed_time_seconds: Number(data.seed_time_seconds || 0),
      stop_after_download: Boolean(data.stop_after_download),
    });
    closeModal("new-modal");
    await refreshFoundation();
    switchView("downloads");
    toast("Torrent queued", state.torrentInfo?.name || data.url, "success");
  } catch (error) { toast("Torrent not queued", error.message, "error"); }
}

async function archiveAction(kind) {
  const form = document.querySelector("[data-source-form=archive]");
  const data = formObject(form);
  const output = document.getElementById("archive-result");
  if (!data.path) return;
  output.hidden = false;
  output.textContent = `${kind === "inspect" ? "Inspecting" : "Testing"}…`;
  try {
    const result = await api("POST", `/api/v3/archive/${kind}`, { path: data.path, password: data.password || "" });
    output.textContent = JSON.stringify(result, null, 2);
  } catch (error) { output.textContent = error.message; }
}

async function extractArchive(form) {
  const data = formObject(form);
  try {
    await api("POST", "/api/v3/archive/extract", {
      path: data.path, destination_root: data.destination_root || "",
      password: data.password || "", delete_archive: Boolean(data.delete_archive),
    });
    closeModal("new-modal");
    await refreshFoundation();
    switchView("downloads");
    toast("Extraction started", fileNameFromUrl(data.path), "success");
  } catch (error) { toast("Extraction not started", error.message, "error"); }
}

async function scanGrabber(form) {
  const data = formObject(form);
  setBusy(form, true);
  try {
    let result;
    if (data.mode === "crawl") {
      result = await api("POST", "/api/batch/crawl", {
        url: data.url, max_pages: Number(data.max_pages || 10),
        include_videos: Boolean(data.include_videos), include_files: Boolean(data.include_files),
      });
      state.grabResults = result.links || [];
    } else {
      result = await api("POST", "/api/grab", { url: data.url });
      state.grabResults = result.links || [];
    }
    renderGrabber();
  } catch (error) { toast("LinkGrabber failed", error.message, "error"); }
  finally { setBusy(form, false); }
}

async function queueGrabbed() {
  const checked = [...document.querySelectorAll("[data-grab-index]:checked")].map(item => state.grabResults[Number(item.dataset.grabIndex)]).filter(Boolean);
  if (!checked.length) return toast("Nothing selected", "Select one or more resources.", "warning");
  const errors = [];
  let started = 0;
  for (const item of checked) {
    try {
      const type = item.type || item.ext;
      if (type === "video") await api("POST", "/api/v3/media/start", { url: item.url, target_dir: state.settings.default_dir });
      else if (type === "torrent" || String(item.url).startsWith("magnet:")) await api("POST", "/api/v3/torrent/start", { url: item.url, target_dir: state.settings.default_dir, stop_after_download: true });
      else await api("POST", "/api/downloads/start", { url: item.url, target_dir: state.settings.default_dir, filename: item.filename || "", duplicate_policy: "reuse" });
      started++;
    } catch (error) { errors.push(error.message); }
  }
  await refreshFoundation();
  toast("LinkGrabber queued", `${started} task${started === 1 ? "" : "s"}`, started ? "success" : "error");
  if (errors.length) toast("Some resources failed", errors.join(" · "), "error");
}

async function saveGeneralSettings(form) {
  const data = formObject(form);
  try {
    await Promise.all([
      api("POST", "/api/settings/concurrent", { value: Number(data.max_concurrent) }),
      api("POST", "/api/settings/connections", { value: Number(data.default_connections) }),
      api("POST", "/api/settings/completion-action", { action: data.completion_action }),
    ]);
    await refreshFoundation();
    toast("Settings saved", "Transfer limits updated.", "success");
  } catch (error) { toast("Settings not saved", error.message, "error"); }
}

async function saveStorageSettings(form) {
  const data = formObject(form);
  try {
    await Promise.all([
      api("POST", "/api/settings/default-dir", { dir: data.default_dir }),
      api("POST", "/api/settings/temp-dir", { dir: data.temp_dir }),
    ]);
    await refreshFoundation();
    toast("Locations saved", "New tasks will use these folders.", "success");
  } catch (error) { toast("Locations not saved", error.message, "error"); }
}

async function saveHostProfile(form) {
  const data = formObject(form);
  const id = `site-${String(data.host_pattern).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
  try {
    await api("POST", "/api/host-profiles", {
      profile: {
        id, name: data.name, host_pattern: data.host_pattern,
        max_connections: Number(data.max_connections || 0),
        speed_limit_bps: Number(data.speed_limit_bps || 0),
        intercept_mode: data.intercept_mode, proxy_url: data.proxy_url || "", enabled: true,
      },
      username: data.username || "", password: data.password || "",
    });
    await refreshFoundation();
    renderSettings();
    switchSettingsTab("sites");
    toast("Site profile saved", data.host_pattern, "success");
  } catch (error) { toast("Profile not saved", error.message, "error"); }
}

async function generatePairingCode(form) {
  const data = formObject(form);
  try {
    const result = await api("POST", "/api/v4/security/pairing", { client_name: data.client_name, role: data.role, expires_in: 600 });
    const output = document.getElementById("pair-code-output");
    output.innerHTML = `<div style="margin-top:14px"><div class="pair-code">${esc(result.code)}</div><p style="font-size:10px;color:var(--muted)">Enter this code in the Lumi browser extension or remote client before ${esc(result.expires_at)}.</p></div>`;
    await loadPairedClients();
  } catch (error) { toast("Code not generated", error.message, "error"); }
}

async function loadPairedClients() {
  try {
    const result = await api("GET", "/api/v4/security/clients");
    const element = document.getElementById("paired-clients");
    if (!element) return;
    element.innerHTML = result.clients.length ? result.clients.map(client => `<div class="client-row"><div><strong>${esc(client.client_name)}</strong><small>${esc(client.created_at)} · last seen ${esc(client.last_seen_at || "never")}</small></div><span class="tag">${esc(client.role)}${client.revoked ? " · revoked" : ""}</span>${client.revoked ? `<span></span>` : `<button class="icon-btn" data-action="revoke-client" data-id="${esc(client.id)}">×</button>`}</div>`).join("") : `<div class="empty">No paired clients.</div>`;
  } catch (error) { toast("Clients unavailable", error.message, "error"); }
}

async function revokeClient(id) {
  if (!confirm("Revoke this client immediately?")) return;
  try { await api("DELETE", `/api/v4/security/clients/${encodeURIComponent(id)}`); await loadPairedClients(); toast("Client revoked", "The token can no longer access Lumi.", "success"); }
  catch (error) { toast("Client not revoked", error.message, "error"); }
}

async function deleteHostProfile(id) {
  if (!confirm("Delete this site profile?")) return;
  try { await api("DELETE", `/api/host-profiles/${encodeURIComponent(id)}`); await refreshFoundation(); renderSettings(); switchSettingsTab("sites"); }
  catch (error) { toast("Profile not deleted", error.message, "error"); }
}

function switchSettingsTab(tab) {
  document.querySelectorAll(".settings-nav button").forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
  document.querySelectorAll(".settings-section").forEach(section => section.classList.toggle("active", section.dataset.settingsSection === tab));
  if (tab === "security") void loadPairedClients();
}

async function diagnosticAction(action) {
  const output = document.getElementById("diagnostic-result");
  if (output) output.textContent = "Working…";
  try {
    let result;
    if (action === "export") result = await api("POST", "/api/v4/diagnostics/export", {});
    if (action === "backup") result = await api("POST", "/api/v4/maintenance/database/backup", { label: "ui" });
    if (action === "repair") {
      if (!confirm("Create a backup, checkpoint, reindex, analyse and vacuum the Lumi database?")) return;
      result = await api("POST", "/api/v4/maintenance/database/repair", {});
    }
    if (action === "recovery") result = await api("POST", "/api/v4/maintenance/database/recovery-export", {});
    if (action === "missing") result = await api("POST", "/api/v4/maintenance/missing-files", { mark: true });
    if (output) output.textContent = JSON.stringify(result, null, 2);
    state.diagnostics = null;
    toast("Maintenance complete", result.path || result.status || `${result.missing_count || 0} missing files`, "success");
  } catch (error) { if (output) output.textContent = error.message; toast("Maintenance failed", error.message, "error"); }
}

async function bulkAction(action) {
  const endpoint = action === "clear" ? "/api/downloads/clear" : `/api/downloads/${action}`;
  try { await api("POST", endpoint, {}); await refreshFoundation(); renderCurrentView(); }
  catch (error) { toast("Bulk action failed", error.message, "error"); }
}

async function toggleQueue(id, active) {
  try { await api("PATCH", `/api/queues/${encodeURIComponent(id)}`, { active }); await refreshFoundation(); renderQueues(); }
  catch (error) { toast("Queue not updated", error.message, "error"); }
}

async function deleteQueue(id) {
  if (!confirm("Delete this queue? Its tasks will move to the Main queue.")) return;
  try { await api("DELETE", `/api/queues/${encodeURIComponent(id)}`); await refreshFoundation(); renderQueues(); }
  catch (error) { toast("Queue not deleted", error.message, "error"); }
}

async function deleteCategory(id) {
  if (!confirm("Delete this category rule? Existing files will not be moved.")) return;
  try { await api("DELETE", `/api/categories/${encodeURIComponent(id)}`); await refreshFoundation(); renderCategories(); }
  catch (error) { toast("Category not deleted", error.message, "error"); }
}

function showContextMenu(taskId, x, y) {
  const task = state.tasks.find(item => item.id === taskId);
  if (!task) return;
  const menu = document.getElementById("context-menu");
  const actions = contextActions(task);
  menu.innerHTML = actions.map(item => item === "separator" ? "<hr>" : `<button class="${item.danger ? "danger" : ""}" data-action="${item.action}" data-task="${esc(task.id)}"><span>${item.icon}</span>${esc(item.label)}</button>`).join("");
  menu.hidden = false;
  const width = 210, height = Math.min(500, actions.length * 36 + 20);
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, x))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - height - 8, y))}px`;
}

function contextActions(task) {
  const items = [{ action: "inspect", label: "Properties & details", icon: "▤" }];
  if (["running","resolving","queued","post_processing"].includes(task.status)) items.push({ action: "pause", label: "Pause", icon: "Ⅱ" });
  if (["paused","failed","cancelled"].includes(task.status)) items.push({ action: task.status === "failed" || task.status === "cancelled" ? "retry" : "resume", label: task.status === "paused" ? "Resume" : "Retry", icon: "▶" });
  if (!["completed","failed","cancelled","paused","needs_link"].includes(task.status)) items.push({ action: "cancel", label: "Cancel", icon: "×", danger: true });
  if (["needs_link","paused","failed"].includes(task.status)) items.push({ action: "repair-link", label: "Repair Download Link", icon: "⌁" });
  items.push("separator", { action: "move-queue", label: "Move to queue", icon: "☷" }, { action: "priority", label: "Change priority", icon: "↕" });
  if (task.status === "completed") items.push({ action: "open", label: "Open file or folder", icon: "↗" }, { action: "move", label: "Move or rename", icon: "→" }, { action: "verify", label: "Verify checksum", icon: "✓" });
  if (archiveLike(task)) items.push({ action: "extract", label: "Extract archive", icon: "▣" });
  if (task.metadata?.file_missing) items.push({ action: "locate", label: "Locate completed file", icon: "⌕" });
  items.push("separator", { action: "remove", label: "Remove task", icon: "−", danger: true }, { action: "remove-file", label: "Remove and delete file", icon: "⌫", danger: true });
  return items;
}

function hideContextMenu() { document.getElementById("context-menu").hidden = true; }

async function handleTaskAction(action, taskId) {
  const task = state.tasks.find(item => item.id === taskId) || state.inspector?.task;
  if (!task) return;
  try {
    if (action === "inspect") return openInspector(taskId);
    if (["pause","resume","retry","cancel"].includes(action)) await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/${action}`, {});
    if (action === "repair-link") {
      await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/repair-wait`, { original_page: task.request?.original_page || "" });
      toast("Waiting for replacement link", "Start the same download again in the paired browser extension.", "warning");
    }
    if (action === "open") await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/open`, {});
    if (action === "move") {
      const targetDir = prompt("Move to folder:", task.target_dir || "");
      if (targetDir === null) return;
      const filename = prompt("Filename:", task.filename || "");
      if (filename === null) return;
      await api("POST", `/api/v4/tasks/${encodeURIComponent(taskId)}/move`, { target_dir: targetDir, filename });
    }
    if (action === "locate") {
      const path = prompt("Enter the current file or folder path:", "");
      if (!path) return;
      await api("POST", `/api/v4/tasks/${encodeURIComponent(taskId)}/locate`, { path });
    }
    if (action === "verify") {
      const algorithm = prompt("Hash algorithm (sha256, sha1, md5):", "sha256");
      if (!algorithm) return;
      const hash = prompt(`Expected ${algorithm.toUpperCase()} hash:`, "");
      if (!hash) return;
      const result = await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/verify`, { algo: algorithm, hash });
      toast("Checksum result", `${result.status}: ${result.actual || ""}`, result.status === "ok" ? "success" : "warning");
    }
    if (action === "extract") {
      await api("POST", "/api/v3/archive/extract", { path: task.path || task.final_path, task_id: task.id, destination_root: task.target_dir });
      toast("Extraction queued", task.filename, "success");
    }
    if (action === "move-queue") {
      const options = state.queues.map(queue => `${queue.id} (${queue.name})`).join("\n");
      const queueId = prompt(`Queue ID:\n${options}`, task.queue_id || "default");
      if (!queueId) return;
      await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/queue`, { queue_id: queueId.trim().split(" ")[0] });
    }
    if (action === "priority") {
      const priority = prompt("Priority (higher numbers run first):", String(task.priority || 0));
      if (priority === null) return;
      await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/priority`, { priority: Number(priority) });
    }
    if (action === "remove" || action === "remove-file") {
      const deleting = action === "remove-file";
      if (!confirm(deleting ? "Remove this task and delete its downloaded file?" : "Remove this task from Lumi?")) return;
      await api("POST", `/api/downloads/${encodeURIComponent(taskId)}/delete`, { delete_file: deleting });
      if (state.inspector?.task?.id === taskId) closeInspector();
    }
    await refreshFoundation();
    renderCurrentView();
    if (state.inspector?.task?.id === taskId) await refreshInspector();
  } catch (error) { toast("Action failed", error.message, "error"); }
}

async function openInspector(taskId) {
  state.inspectorTab = "overview";
  document.getElementById("drawer-backdrop").hidden = false;
  document.getElementById("inspector").hidden = false;
  document.getElementById("inspector-body").innerHTML = `<div class="empty">Loading task details…</div>`;
  await refreshInspector();
}

async function refreshInspector(render = true) {
  const currentId = state.inspector?.task?.id || document.querySelector(".download-row[data-task]:hover")?.dataset.task;
  const taskId = currentId || state.inspector?.task?.id;
  if (!taskId) {
    const title = document.getElementById("inspector-title");
    const possible = state.inspector?.task?.id;
    if (!possible) return;
  }
  const id = taskId || state.inspector.task.id;
  try {
    state.inspector = await api("GET", `/api/v4/tasks/${encodeURIComponent(id)}/inspector`);
    if (render) renderInspector();
  } catch (error) { document.getElementById("inspector-body").innerHTML = emptyState("Inspector unavailable", error.message); }
}

function renderInspector() {
  const data = state.inspector;
  if (!data) return;
  text("inspector-title", data.task.filename || data.task.id);
  document.querySelectorAll("#inspector-tabs button").forEach(button => button.classList.toggle("active", button.dataset.tab === state.inspectorTab));
  const body = document.getElementById("inspector-body");
  if (state.inspectorTab === "overview") body.innerHTML = inspectorOverview(data);
  if (state.inspectorTab === "connections") body.innerHTML = inspectorConnections(data);
  if (state.inspectorTab === "request") body.innerHTML = `<pre class="json-view">${esc(JSON.stringify(data.request, null, 2))}</pre>`;
  if (state.inspectorTab === "queue") body.innerHTML = inspectorQueue(data);
  if (state.inspectorTab === "files") body.innerHTML = inspectorFiles(data);
  if (state.inspectorTab === "post") body.innerHTML = `<pre class="json-view">${esc(JSON.stringify(data.post_processing || {}, null, 2))}</pre>`;
  if (state.inspectorTab === "log") body.innerHTML = inspectorLog(data);
}

function inspectorOverview(data) {
  const task = data.task;
  const warning = data.overview?.warning;
  return `${warning ? `<div class="warning-box">${esc(warning)}</div>` : ""}<div class="detail-grid">${detailBox("Status", statusLabel(task.status))}${detailBox("Progress", `${progress(task).toFixed(2)}%`)}${detailBox("Downloaded", fmtBytes(task.downloaded_bytes || 0))}${detailBox("Total size", fmtBytes(task.total_bytes || 0))}${detailBox("Speed", fmtRate(task.speed_bytes_per_sec || 0))}${detailBox("Connections", task.connections || 1)}${detailBox("Category", task.category_id || "other")}${detailBox("Queue", task.queue_id || "default")}${detailBox("Created", fmtDate(task.created_at))}${detailBox("Finished", task.finished_at ? fmtDate(task.finished_at) : "—")}${detailBox("Final file", task.path || task.final_path || "—")}${detailBox("Temporary file", task.partial_path || "—")}</div><div class="page-tools" style="margin-top:14px"><button class="btn" data-action="${task.status === "paused" ? "resume" : "pause"}">${task.status === "paused" ? "▶ Resume" : "Ⅱ Pause"}</button>${task.status === "completed" ? `<button class="btn" data-action="open">Open</button><button class="btn" data-action="move">Move / rename</button>` : ""}${["needs_link","paused","failed"].includes(task.status) ? `<button class="btn" data-action="repair-link">Repair link</button>` : ""}</div>${task.error ? `<div class="warning-box" style="border-color:rgba(255,116,125,.3);background:rgba(255,116,125,.08);color:#ffabb0">${esc(task.error)}</div>` : ""}`;
}

function detailBox(label, value) { return `<div class="detail-box"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`; }

function inspectorConnections(data) {
  const segments = data.connections || [];
  if (!segments.length) return emptyState("No segment map", "This source is using one stream or has not started yet.");
  return `<div class="connection-map">${segments.map((segment,index) => {
    const length = Math.max(1, Number(segment.end) - Number(segment.start) + 1);
    const pct = Math.min(100, Number(segment.downloaded || 0) / length * 100);
    return `<div class="connection-row"><span>#${index + 1} ${esc(segment.status || "pending")}</span><div class="range-track"><div class="range-fill" style="width:${pct}%"></div></div><strong>${pct.toFixed(0)}%</strong></div>`;
  }).join("")}</div>`;
}

function inspectorQueue(data) {
  const queue = data.queue || {};
  return `<div class="summary-list"><div class="summary-row"><span>Queue</span><strong>${esc(queue.name || data.task.queue_id)}</strong></div><div class="summary-row"><span>Queue active</span><strong>${queue.active ? "Yes" : "No"}</strong></div><div class="summary-row"><span>Queue limit</span><strong>${queue.max_running || "Global"}</strong></div><div class="summary-row"><span>Task priority</span><strong>${data.task.priority || 0}</strong></div></div><form class="form-stack" data-inspector-form="queue" style="margin-top:15px"><label>Move to queue<select class="select" name="queue_id">${state.queues.map(item => `<option value="${esc(item.id)}" ${item.id === data.task.queue_id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label><label>Priority<input class="input" name="priority" type="number" value="${esc(data.task.priority || 0)}"></label><button class="btn primary" type="submit">Update queue settings</button></form>`;
}

function inspectorFiles(data) {
  if (!data.files?.length) return `<div class="summary-list"><div class="summary-row"><span>Recorded output</span><strong>${esc(data.task.path || data.task.final_path || "—")}</strong></div><div class="summary-row"><span>Exists</span><strong>${data.overview?.file_exists ? "Yes" : "No"}</strong></div></div>`;
  return `<div class="file-select-list">${data.files.map((file,index) => `<div class="option-row"><span>${index + 1}</span><div><strong>${esc(file.name || file.path || `File ${index + 1}`)}</strong><small>${esc(file.path || "")} ${file.size ? `· ${fmtBytes(file.size)}` : ""}</small></div><span>${file.selected === false ? "Skipped" : file.exists === false ? "Missing" : "Ready"}</span></div>`).join("")}</div>`;
}

function inspectorLog(data) {
  if (!data.events?.length) return emptyState("No event history", "Task events will appear here.");
  return `<div class="event-list">${data.events.map(event => `<div class="event-row"><time>${esc(fmtDate(event.created_at))}</time><div><strong>${esc(humanize(event.event_type))}</strong>${event.payload && Object.keys(event.payload).length ? `<pre>${esc(JSON.stringify(event.payload, null, 2))}</pre>` : ""}</div></div>`).join("")}</div>`;
}

async function handleInspectorSubmit(event) {
  event.preventDefault();
  if (!state.inspector) return;
  if (event.target.dataset.inspectorForm === "queue") {
    const data = formObject(event.target);
    try {
      await Promise.all([
        api("POST", `/api/downloads/${encodeURIComponent(state.inspector.task.id)}/queue`, { queue_id: data.queue_id }),
        api("POST", `/api/downloads/${encodeURIComponent(state.inspector.task.id)}/priority`, { priority: Number(data.priority || 0) }),
      ]);
      await refreshFoundation();
      await refreshInspector();
      toast("Queue settings updated", state.inspector.task.filename, "success");
    } catch (error) { toast("Queue settings failed", error.message, "error"); }
  }
}

function closeInspector() {
  document.getElementById("inspector").hidden = true;
  document.getElementById("drawer-backdrop").hidden = true;
  state.inspector = null;
}

function openModal(id) { document.getElementById(id).hidden = false; }
function closeModal(id) { document.getElementById(id).hidden = true; }

function emptyState(title, note) { return `<div class="empty"><div class="empty-icon">⌁</div><strong>${esc(title)}</strong><span>${esc(note)}</span></div>`; }
function statusLabel(value) { return humanize(String(value || "unknown")); }
function humanize(value) { return String(value || "").replace(/[_-]+/g," ").replace(/\b\w/g, char => char.toUpperCase()); }
function progress(task) { if (task.status === "completed") return 100; const value = Number(task.progress_percent || 0); return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0)); }
function archiveLike(task) { return task.type === "archive" || /\.(zip|rar|7z|tar|gz|bz2|xz|zst|001)$/i.test(task.filename || task.path || ""); }
function formatLabel(format) { return [format.format_note || format.format || format.format_id, format.height ? `${format.height}p` : "", format.fps ? `${format.fps}fps` : "", format.ext, format.filesize ? fmtBytes(format.filesize) : ""].filter(Boolean).join(" · "); }
function fileNameFromUrl(value) { try { return decodeURIComponent(new URL(value, location.href).pathname.split("/").pop()) || value; } catch { return String(value).split(/[\\/]/).pop(); } }
function splitList(value) { return String(value || "").split(/[,\s]+/).map(item => item.trim()).filter(Boolean); }
function formObject(form) { const result = {}; for (const [key,value] of new FormData(form).entries()) { if (result[key] !== undefined) result[key] = Array.isArray(result[key]) ? [...result[key],value] : [result[key],value]; else result[key] = value; } form.querySelectorAll("input[type=checkbox]").forEach(input => { if (!input.name) return; if (!input.checked && result[input.name] === undefined) result[input.name] = false; if (input.checked && result[input.name] === undefined) result[input.name] = true; }); return result; }
function setBusy(form, busy) { form?.querySelectorAll("button,input,select,textarea").forEach(element => element.disabled = busy); }
function text(id, value) { const element = document.getElementById(id); if (element) element.textContent = String(value ?? ""); }
function esc(value) { return String(value ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }
function fmtBytes(value) { const bytes = Number(value || 0); if (!bytes) return "0 B"; const units=["B","KB","MB","GB","TB","PB"]; const index=Math.min(units.length-1,Math.floor(Math.log(bytes)/Math.log(1024))); return `${(bytes/1024**index).toFixed(index ? (bytes/1024**index>=100?0:1) : 0)} ${units[index]}`; }
function fmtRate(value) { return `${fmtBytes(value)}/s`; }
function fmtDuration(seconds) { const value=Math.max(0,Number(seconds||0)); if (!value) return "—"; const h=Math.floor(value/3600),m=Math.floor(value%3600/60),s=Math.floor(value%60); return h?`${h}h ${m}m`:m?`${m}m ${s}s`:`${s}s`; }
function fmtDate(value) { if (!value) return "—"; const date=new Date(value); return Number.isNaN(date.getTime())?String(value):date.toLocaleString(); }
function toast(title, message, type="info") { const stack=document.getElementById("toast-stack"); const element=document.createElement("div"); element.className=`toast ${type}`; element.innerHTML=`<div></div><div><strong>${esc(title)}</strong><small>${esc(message)}</small></div><button>×</button>`; element.querySelector("button").addEventListener("click",()=>element.remove()); stack.append(element); setTimeout(()=>element.remove(),6500); }
