"use strict";

/* THETECHGUY App Shell Standard v1 */
(() => {
  if (!window.electronApp?.isElectron) return;

  const shellState = {
    notifications: [],
    update: null,
    seen: new Set(JSON.parse(localStorage.getItem("TTG.shell.seen") || "[]")),
  };

  window.addEventListener("DOMContentLoaded", initShell, { once: true });

  function initShell() {
    document.body.classList.add("ttg-desktop");
    document.body.insertAdjacentHTML("afterbegin", shellHtml());
    bindShell();
    void syncWindowState();
    refreshNotifications();
    setInterval(refreshNotifications, 1800);
    window.electronApp.onWindowState?.(applyWindowState);
    window.electronApp.onUpdateStatus?.(status => {
      shellState.update = status || null;
      refreshNotifications();
    });
  }

  function shellHtml() {
    return `
      <header class="ttg-titlebar" id="ttg-titlebar">
        <div class="ttg-titlebar-brand"><img src="/static/favicon-96.png" alt="Lumi"><strong>Lumi DM</strong><small>THETECHGUY TOOL</small></div>
        <div class="ttg-titlebar-spacer"></div>
        <div class="ttg-titlebar-actions">
          <button class="ttg-titlebar-btn" id="ttg-bell" type="button" title="Notifications" aria-label="Notifications">${bellIcon()}<b class="ttg-titlebar-badge" id="ttg-bell-badge" hidden>0</b></button>
          <button class="ttg-titlebar-btn" id="ttg-gear" type="button" title="Settings and help" aria-label="Settings and help">${gearIcon()}</button>
          <i class="ttg-titlebar-divider"></i>
          <button class="ttg-titlebar-btn" data-window-action="minimize" type="button" title="Minimize" aria-label="Minimize">${minimizeIcon()}</button>
          <button class="ttg-titlebar-btn" id="ttg-maximize" data-window-action="maximize" type="button" title="Maximize" aria-label="Maximize">${maximizeIcon()}</button>
          <button class="ttg-titlebar-btn close" data-window-action="close" type="button" title="Close" aria-label="Close">${closeIcon()}</button>
        </div>
      </header>
      <section class="ttg-shell-menu ttg-notification-menu" id="ttg-notification-menu" hidden>
        <div class="ttg-shell-menu-head"><strong>Notifications</strong><small>Downloads, warnings and updates</small></div>
        <div id="ttg-notification-list"></div>
      </section>
      <section class="ttg-shell-menu" id="ttg-gear-menu" hidden>
        <div class="ttg-shell-menu-head"><strong>Lumi controls</strong><small>One place for settings and support</small></div>
        <button type="button" data-shell-action="settings"><span>⚙</span><span>Settings</span></button>
        <button type="button" data-shell-action="update"><span>↻</span><span>Check for updates</span></button>
        <button type="button" data-shell-action="help"><span>?</span><span>Help</span></button>
        <button type="button" data-shell-action="about"><span>ⓘ</span><span>About Lumi</span></button>
        <hr>
        <button type="button" data-shell-action="diagnostics"><span>＋</span><span>Advanced diagnostics</span></button>
      </section>
      <div class="ttg-shell-modal-backdrop" id="ttg-shell-modal" hidden>
        <section class="ttg-shell-modal" role="dialog" aria-modal="true" aria-labelledby="ttg-shell-modal-title">
          <div class="ttg-shell-modal-head"><h2 id="ttg-shell-modal-title">Lumi</h2><button class="ttg-shell-modal-close" type="button" aria-label="Close">×</button></div>
          <div class="ttg-shell-modal-body" id="ttg-shell-modal-body"></div>
        </section>
      </div>`;
  }

  function bindShell() {
    document.querySelectorAll("[data-window-action]").forEach(button => {
      button.addEventListener("click", async () => {
        const result = await window.electronApp.windowControl(button.dataset.windowAction);
        if (result) applyWindowState(result);
      });
    });
    document.getElementById("ttg-bell")?.addEventListener("click", event => {
      event.stopPropagation();
      toggleMenu("notifications");
    });
    document.getElementById("ttg-gear")?.addEventListener("click", event => {
      event.stopPropagation();
      toggleMenu("gear");
    });
    document.getElementById("ttg-gear-menu")?.addEventListener("click", event => {
      const button = event.target.closest("[data-shell-action]");
      if (!button) return;
      closeMenus();
      void handleShellAction(button.dataset.shellAction);
    });
    document.getElementById("ttg-notification-list")?.addEventListener("click", event => {
      const button = event.target.closest("[data-notification-index]");
      if (!button) return;
      const item = shellState.notifications[Number(button.dataset.notificationIndex)];
      if (!item) return;
      markSeen(item.id);
      closeMenus();
      routeNotification(item);
    });
    document.querySelector("#ttg-shell-modal .ttg-shell-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("ttg-shell-modal")?.addEventListener("click", event => {
      if (event.target.id === "ttg-shell-modal") closeModal();
    });
    document.addEventListener("click", event => {
      if (!event.target.closest(".ttg-shell-menu") && !event.target.closest("#ttg-bell") && !event.target.closest("#ttg-gear")) closeMenus();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") { closeMenus(); closeModal(); }
    });
  }

  async function syncWindowState() {
    try { applyWindowState(await window.electronApp.getWindowState()); } catch (_) {}
  }

  function applyWindowState(value = {}) {
    const button = document.getElementById("ttg-maximize");
    if (!button) return;
    button.innerHTML = value.maximized ? restoreIcon() : maximizeIcon();
    button.title = value.maximized ? "Restore" : "Maximize";
    document.body.classList.toggle("ttg-window-maximized", Boolean(value.maximized));
  }

  function toggleMenu(which) {
    const bell = document.getElementById("ttg-notification-menu");
    const gear = document.getElementById("ttg-gear-menu");
    const bellButton = document.getElementById("ttg-bell");
    const gearButton = document.getElementById("ttg-gear");
    if (which === "notifications") {
      const opening = bell.hidden;
      bell.hidden = !opening;
      gear.hidden = true;
      bellButton.classList.toggle("active", opening);
      gearButton.classList.remove("active");
      if (opening) {
        shellState.notifications.forEach(item => markSeen(item.id, false));
        renderNotifications();
        persistSeen();
        updateBadge();
      }
    } else {
      const opening = gear.hidden;
      gear.hidden = !opening;
      bell.hidden = true;
      gearButton.classList.toggle("active", opening);
      bellButton.classList.remove("active");
    }
  }

  function closeMenus() {
    document.getElementById("ttg-notification-menu").hidden = true;
    document.getElementById("ttg-gear-menu").hidden = true;
    document.getElementById("ttg-bell")?.classList.remove("active");
    document.getElementById("ttg-gear")?.classList.remove("active");
  }

  async function handleShellAction(action) {
    if (action === "settings") return switchShellView("settings");
    if (action === "diagnostics") return switchShellView("diagnostics");
    if (action === "update") {
      try {
        const result = await window.electronApp.checkForUpdates(true);
        showModal("Application update", `<h3>${esc(result.available ? `Lumi ${result.version} is available` : "Lumi is up to date")}</h3><p>${esc(result.message || "The GitHub Releases check completed.")}</p>`);
      } catch (error) {
        showModal("Update check failed", `<p>${esc(error.message)}</p>`);
      }
      return;
    }
    if (action === "help") return showHelp();
    if (action === "about") return void showAbout();
  }

  function switchShellView(view) {
    if (typeof switchView === "function") switchView(view);
  }

  function showHelp() {
    showModal("Lumi Help", `
      <h3>Browser downloads</h3><p>Install and pair the Lumi browser extension. Matching downloads pause in the browser only after Lumi safely stores the request, then the corner setup asks for the filename, folder and queue.</p>
      <h3>Firmware and operating systems</h3><p>Use the Firmware workspace to choose a device or computer operating system. Confirm the source, version, architecture and checksum before starting the download.</p>
      <h3>Corner widget</h3><p>The permanent widget shows live internet speed while idle and download progress while Lumi is working. Click it for pause, resume, cancel and open controls.</p>
      <h3>Repair and recovery</h3><p>Use Advanced diagnostics only for database backup, repair, missing-file checks and support evidence.</p>`);
  }

  async function showAbout() {
    let info = { name: "Lumi DM", version: "", publisher: "THETECHGUY DIGITAL SOLUTIONS", website: "thetechguyds.com" };
    try { info = { ...info, ...(await window.electronApp.getAppInfo()) }; } catch (_) {}
    showModal("About Lumi", `
      <div class="ttg-about-brand"><img src="/static/favicon-96.png" alt="Lumi"><div><strong>${esc(info.name || "Lumi DM")}</strong><small>Version ${esc(info.version || "development")}</small></div></div>
      <p><strong>${esc(info.publisher)}</strong><br>${esc(info.website)}</p>
      <p>Lumi is the THETECHGUY multi-source download manager for direct files, browser capture, media, torrents, firmware and operating-system images.</p>
      <p>ONE BRAND • ALL SOLUTIONS</p>`);
  }

  function showModal(title, body) {
    document.getElementById("ttg-shell-modal-title").textContent = title;
    document.getElementById("ttg-shell-modal-body").innerHTML = body;
    document.getElementById("ttg-shell-modal").hidden = false;
  }

  function closeModal() {
    const modal = document.getElementById("ttg-shell-modal");
    if (modal) modal.hidden = true;
  }

  function refreshNotifications() {
    const items = [];
    const tasks = typeof state !== "undefined" && Array.isArray(state.tasks) ? state.tasks : [];
    for (const task of tasks) {
      const name = task.filename || task.metadata?.title || task.url || "Download";
      if (task.status === "browser_pending" || task.status === "staged") {
        items.push({ id: `task:${task.id}:${task.status}`, kind: "pending", title: "Download needs confirmation", detail: name, taskId: task.id, view: "unfinished" });
      } else if (task.status === "failed") {
        items.push({ id: `task:${task.id}:failed`, kind: "failed", title: "Download failed", detail: `${name}${task.error ? ` · ${task.error}` : ""}`, taskId: task.id, view: "unfinished" });
      } else if (task.status === "completed") {
        items.push({ id: `task:${task.id}:completed`, kind: "completed", title: "Download complete", detail: name, taskId: task.id, view: "finished" });
      }
    }
    if (shellState.update?.state === "available" || shellState.update?.state === "ready" || shellState.update?.available) {
      items.unshift({ id: `update:${shellState.update.version || "new"}:${shellState.update.state || "available"}`, kind: "update", title: shellState.update.state === "ready" ? "Update ready to install" : "Lumi update available", detail: shellState.update.version ? `Version ${shellState.update.version}` : "Open the updater", action: "update" });
    }
    shellState.notifications = items.slice(0, 25);
    renderNotifications();
    updateBadge();
  }

  function renderNotifications() {
    const element = document.getElementById("ttg-notification-list");
    if (!element) return;
    if (!shellState.notifications.length) {
      element.innerHTML = `<div class="ttg-notification-empty">Nothing needs your attention.</div>`;
      return;
    }
    element.innerHTML = shellState.notifications.map((item, index) => `
      <button class="ttg-notification-item" type="button" data-notification-index="${index}">
        <span class="ttg-notification-dot ${esc(item.kind)}"></span>
        <span class="ttg-notification-copy"><strong>${esc(item.title)}</strong><small>${esc(item.detail)}</small></span>
      </button>`).join("");
  }

  function updateBadge() {
    const count = shellState.notifications.filter(item => !shellState.seen.has(item.id)).length;
    const badge = document.getElementById("ttg-bell-badge");
    if (!badge) return;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
  }

  function markSeen(id, save = true) {
    shellState.seen.add(id);
    if (save) persistSeen();
    updateBadge();
  }

  function persistSeen() {
    const active = new Set(shellState.notifications.map(item => item.id));
    const compact = [...shellState.seen].filter(id => active.has(id)).slice(-100);
    shellState.seen = new Set(compact);
    localStorage.setItem("TTG.shell.seen", JSON.stringify(compact));
  }

  function routeNotification(item) {
    if (item.action === "update") return void handleShellAction("update");
    if (item.view) switchShellView(item.view);
    if (item.taskId && typeof openInspector === "function") setTimeout(() => openInspector(item.taskId), 80);
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
  }

  function bellIcon() { return `<svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M10 21h4"></path></svg>`; }
  function gearIcon() { return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z"></path></svg>`; }
  function minimizeIcon() { return `<svg viewBox="0 0 24 24"><path d="M6 12h12"></path></svg>`; }
  function maximizeIcon() { return `<svg viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="1"></rect></svg>`; }
  function restoreIcon() { return `<svg viewBox="0 0 24 24"><rect x="5" y="8" width="10" height="10" rx="1"></rect><path d="M9 8V5h10v10h-4"></path></svg>`; }
  function closeIcon() { return `<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7 7 17"></path></svg>`; }
})();
