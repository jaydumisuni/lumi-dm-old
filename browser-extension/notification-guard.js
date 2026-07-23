"use strict";

/**
 * Lumi browser notification guard.
 *
 * Connectivity failures are state, not individual events. The legacy bridge
 * created a new Windows notification for every failed request, so one offline
 * server could fill Action Center in seconds. This guard loads before the
 * browser bridge and enforces these rules for both old and current messages:
 *
 * - automatic connectivity failures stay quiet and only set the extension badge;
 * - a manual connectivity warning may appear once per cooldown, under one ID;
 * - exact duplicate normal notifications are collapsed for a short interval;
 * - successful Lumi handoffs clear the offline badge and connectivity notice.
 */
(() => {
  if (!globalThis.chrome?.notifications?.create) return;

  const CONNECTIVITY_ID = "LUMIDM-connectivity-state";
  const STORAGE_KEY = "LUMIDM-connectivity-v6";
  const OFFLINE_NOTICE_COOLDOWN_MS = 15 * 60 * 1000;
  const DUPLICATE_WINDOW_MS = 8 * 1000;
  const nativeCreate = chrome.notifications.create.bind(chrome.notifications);
  const nativeClear = chrome.notifications.clear.bind(chrome.notifications);
  const recent = new Map();
  let inFlight = Promise.resolve();

  function nativeCreateAsync(id, options) {
    return new Promise((resolve, reject) => {
      const callback = notificationId => {
        const error = chrome.runtime?.lastError;
        if (error) reject(new Error(error.message));
        else resolve(notificationId || id || "");
      };
      if (id) nativeCreate(id, options, callback);
      else nativeCreate(options, callback);
    });
  }

  function clearAsync(id) {
    return new Promise(resolve => nativeClear(id, () => resolve()));
  }

  function parseCreateArgs(args) {
    if (typeof args[0] === "string") {
      return { id: args[0], options: args[1] || {}, callback: args[2] };
    }
    return { id: "", options: args[0] || {}, callback: args[1] };
  }

  function textOf(options) {
    return `${String(options.title || "")} ${String(options.message || "")}`.toLowerCase();
  }

  function isConnectivityFailure(options) {
    return /(not reachable|cannot reach|failed to fetch|network\s*error|networkerror|connection refused|econnrefused|lumi is not ready|became unavailable|load failed|fetch failed|operation was aborted|the user aborted)/i.test(textOf(options));
  }

  function isQuietAutomaticFailure(options) {
    const title = String(options.title || "");
    return /(not reachable|browser kept download|browser kept link)/i.test(title);
  }

  function isRecovery(options) {
    const title = String(options.title || "");
    return /(choose download options|download started|link repaired|lumi dm — started)/i.test(title);
  }

  function duplicateKey(options) {
    return `${String(options.title || "")}\n${String(options.message || "")}`;
  }

  async function readConnectivityState() {
    try {
      const value = await chrome.storage.local.get({ [STORAGE_KEY]: {} });
      return value[STORAGE_KEY] || {};
    } catch {
      return {};
    }
  }

  async function writeConnectivityState(value) {
    try { await chrome.storage.local.set({ [STORAGE_KEY]: value }); }
    catch {}
  }

  async function setOfflineBadge() {
    try {
      await chrome.action.setBadgeBackgroundColor({ color: "#b45309" });
      await chrome.action.setBadgeText({ text: "!" });
      await chrome.action.setTitle({ title: "Lumi DM — app offline; browser downloads remain safe" });
    } catch {}
  }

  async function clearOfflineState() {
    await clearAsync(CONNECTIVITY_ID).catch(() => {});
    try {
      await chrome.action.setBadgeText({ text: "" });
      await chrome.action.setTitle({ title: "Lumi DM" });
    } catch {}
    await writeConnectivityState({ reachable: true, recoveredAt: Date.now(), lastNoticeAt: 0 });
  }

  async function handleConnectivityFailure(options) {
    const now = Date.now();
    const state = await readConnectivityState();
    await setOfflineBadge();
    await writeConnectivityState({
      reachable: false,
      failedAt: now,
      lastNoticeAt: Number(state.lastNoticeAt || 0),
      message: String(options.message || "Lumi is unavailable").slice(0, 240),
    });

    // Automatic interceptions must silently fall back to the browser. This is
    // the exact path that produced the screenshot flood.
    if (isQuietAutomaticFailure(options)) return CONNECTIVITY_ID;

    if (now - Number(state.lastNoticeAt || 0) < OFFLINE_NOTICE_COOLDOWN_MS) {
      return CONNECTIVITY_ID;
    }

    const notice = {
      type: "basic",
      iconUrl: options.iconUrl || "icons/icon48.png",
      title: "Lumi DM — App unavailable",
      message: "Lumi is still starting or offline. The browser kept the download safely.",
      priority: 0,
    };
    await writeConnectivityState({
      reachable: false,
      failedAt: now,
      lastNoticeAt: now,
      message: notice.message,
    });
    return nativeCreateAsync(CONNECTIVITY_ID, notice);
  }

  async function guardedCreate(id, options) {
    if (isRecovery(options)) await clearOfflineState();
    if (isConnectivityFailure(options)) return handleConnectivityFailure(options);

    const now = Date.now();
    const key = duplicateKey(options);
    const previous = Number(recent.get(key) || 0);
    if (previous && now - previous < DUPLICATE_WINDOW_MS) return id || "LUMIDM-duplicate-collapsed";
    recent.set(key, now);
    for (const [candidate, timestamp] of recent) {
      if (now - timestamp > DUPLICATE_WINDOW_MS) recent.delete(candidate);
    }
    return nativeCreateAsync(id, options);
  }

  chrome.notifications.create = function lumiGuardedNotificationCreate(...args) {
    const { id, options, callback } = parseCreateArgs(args);
    const task = inFlight = inFlight
      .catch(() => {})
      .then(() => guardedCreate(id, options));
    if (typeof callback === "function") {
      task.then(value => callback(value)).catch(() => callback(""));
      return undefined;
    }
    return task;
  };

  // Remove notifications left by the old extension worker when this fixed
  // worker is first loaded. Notification details cannot be queried, so clear
  // only this extension's own current notifications.
  try {
    chrome.notifications.getAll(items => {
      for (const id of Object.keys(items || {})) nativeClear(id, () => {});
    });
  } catch {}
})();
