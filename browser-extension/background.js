/**
 * Lumi DM v2 — secure browser capture and Repair Download Link bridge.
 *
 * Request secrets are sent only to a loopback Lumi server. Browser request state
 * is short-lived and bounded so normal browsing cannot exhaust extension memory.
 */

const DEFAULT_SERVER = "http://localhost:7000";
const REQUEST_TTL_MS = 2 * 60 * 1000;
const CONTROL_TTL_MS = 30 * 1000;
const MAX_CAPTURE_BODY_BYTES = 4 * 1024 * 1024;
const MAX_REQUESTS_PER_URL = 6;

let serverUrl = DEFAULT_SERVER;
let downloadDir = "";
let interceptEnabled = true;
let hostModes = {};
let repairPending = null;

const recentUrls = new Map();
const tabMedia = new Map();
const requestsById = new Map();
const requestsByUrl = new Map();
const attachmentUrls = new Map();
const forceTabs = new Map();
const bypassTabs = new Map();
const bypassUrls = new Map();

const MEDIA_RE = /\.(m3u8|mpd|mp4|webm|mov|flv|ts|avi|mkv)(\?|#|$)/i;
const SKIP_PREFIXES = [
  "blob:", "data:", "chrome://", "chrome-extension://", "moz-extension://",
];
const DOWNLOAD_EXTENSIONS = new Set([
  "zip", "rar", "7z", "gz", "tar", "bz2", "xz", "zst",
  "exe", "msi", "dmg", "pkg", "deb", "rpm", "apk", "ipa", "appx",
  "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "ts", "m2ts",
  "mp3", "flac", "wav", "aac", "ogg", "opus", "m4a",
  "pdf", "epub", "mobi", "azw3", "iso", "img", "bin", "nrg", "torrent",
]);
const WEB_ASSET_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif",
  "tiff", "css", "js", "json", "xml", "html", "htm", "woff", "woff2",
  "ttf", "eot", "map",
]);
const CAPTURE_HEADERS = new Set([
  "accept", "accept-language", "authorization", "content-type", "cookie",
  "origin", "proxy-authorization", "referer", "user-agent",
]);

chrome.alarms.create("LUMIDM-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  chrome.storage.local.get("server");
  cleanupState();
  void refreshRepairPending();
});

chrome.storage.local.get(
  {
    server: DEFAULT_SERVER,
    downloadDir: "",
    interceptEnabled: true,
    hostModes: {},
  },
  values => {
    serverUrl = normaliseServer(values.server);
    downloadDir = values.downloadDir || "";
    interceptEnabled = values.interceptEnabled !== false;
    hostModes = values.hostModes || {};
    void refreshRepairPending();
  },
);

chrome.storage.onChanged.addListener(changes => {
  if (changes.server) serverUrl = normaliseServer(changes.server.newValue);
  if (changes.downloadDir) downloadDir = changes.downloadDir.newValue || "";
  if (changes.interceptEnabled) {
    interceptEnabled = changes.interceptEnabled.newValue !== false;
  }
  if (changes.hostModes) hostModes = changes.hostModes.newValue || {};
});

function normaliseServer(value) {
  return String(value || DEFAULT_SERVER).replace(/\/$/, "");
}

function isLocalServer() {
  try {
    const host = new URL(serverUrl).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1";
  } catch {
    return false;
  }
}

function fetchWithTimeout(url, options = {}, timeout = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  return fetch(url, { ...options, signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

function cleanupState() {
  const now = Date.now();
  for (const map of [recentUrls, attachmentUrls, forceTabs, bypassTabs, bypassUrls]) {
    for (const [key, timestamp] of map) {
      if (now - Number(timestamp || 0) > REQUEST_TTL_MS) map.delete(key);
    }
  }
  for (const [id, value] of requestsById) {
    if (now - value.timestamp > REQUEST_TTL_MS) requestsById.delete(id);
  }
  for (const [url, values] of requestsByUrl) {
    const fresh = values.filter(value => now - value.timestamp <= REQUEST_TTL_MS);
    if (fresh.length) requestsByUrl.set(url, fresh.slice(-MAX_REQUESTS_PER_URL));
    else requestsByUrl.delete(url);
  }
}

function isDuplicate(url) {
  const now = Date.now();
  const previous = recentUrls.get(url);
  if (previous && now - previous < 5000) return true;
  recentUrls.set(url, now);
  return false;
}

function canonicalHeaders(headers = []) {
  const result = {};
  for (const header of headers) {
    const lower = String(header.name || "").toLowerCase();
    if (!CAPTURE_HEADERS.has(lower) || header.value == null) continue;
    const name = lower.split("-")
      .map(part => part ? part[0].toUpperCase() + part.slice(1) : part)
      .join("-");
    result[name] = String(header.value);
  }
  return result;
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

function captureRequestBody(requestBody) {
  if (!requestBody) return { body: null, error: "" };
  if (requestBody.formData) {
    const encoded = JSON.stringify(requestBody.formData);
    if (encoded.length > MAX_CAPTURE_BODY_BYTES) {
      return { body: null, error: "POST form exceeds Lumi's 4 MB capture limit" };
    }
    return { body: { kind: "form", data: requestBody.formData }, error: "" };
  }

  const rawItems = requestBody.raw || [];
  let totalBytes = 0;
  const chunks = [];
  for (const item of rawItems) {
    if (!item.bytes) continue;
    totalBytes += item.bytes.byteLength;
    if (totalBytes > MAX_CAPTURE_BODY_BYTES) {
      return { body: null, error: "POST body exceeds Lumi's 4 MB capture limit" };
    }
    chunks.push(bytesToBase64(item.bytes));
  }
  if (!chunks.length) return { body: null, error: "" };
  return { body: { kind: "base64", data: chunks.join("") }, error: "" };
}

function rememberRequest(details) {
  const current = requestsById.get(details.requestId) || {
    url: details.url,
    final_url: details.url,
    method: details.method || "GET",
    headers: {},
    post_body: null,
    capture_error: "",
    tabId: details.tabId,
    frameId: details.frameId,
    timestamp: Date.now(),
  };
  current.url ||= details.url;
  current.final_url = details.url;
  current.method = details.method || current.method || "GET";
  current.tabId = details.tabId;
  current.frameId = details.frameId;
  current.timestamp = Date.now();

  const captured = captureRequestBody(details.requestBody);
  if (captured.body) current.post_body = captured.body;
  if (captured.error) current.capture_error = captured.error;

  requestsById.set(details.requestId, current);
  const bucket = requestsByUrl.get(details.url) || [];
  bucket.push(current);
  requestsByUrl.set(details.url, bucket.slice(-MAX_REQUESTS_PER_URL));
  return current;
}

chrome.webRequest.onBeforeRequest.addListener(
  details => {
    const current = rememberRequest(details);
    if (details.tabId >= 0 && MEDIA_RE.test(details.url) && details.url.length >= 20) {
      let media = tabMedia.get(details.tabId);
      if (!media) {
        media = new Set();
        tabMedia.set(details.tabId, media);
      }
      media.add(details.url);
      current.media = true;
    }
  },
  { urls: ["<all_urls>"] },
  ["requestBody"],
);

chrome.webRequest.onBeforeSendHeaders.addListener(
  details => {
    const current = requestsById.get(details.requestId) || rememberRequest(details);
    current.headers = {
      ...current.headers,
      ...canonicalHeaders(details.requestHeaders),
    };
    current.timestamp = Date.now();
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onBeforeRedirect.addListener(
  details => {
    const current = requestsById.get(details.requestId);
    if (!current) return;
    current.final_url = details.redirectUrl;
    current.timestamp = Date.now();
    const bucket = requestsByUrl.get(details.redirectUrl) || [];
    bucket.push(current);
    requestsByUrl.set(details.redirectUrl, bucket.slice(-MAX_REQUESTS_PER_URL));
  },
  { urls: ["<all_urls>"] },
);

chrome.webRequest.onHeadersReceived.addListener(
  details => {
    const disposition = (details.responseHeaders || []).find(
      header => String(header.name).toLowerCase() === "content-disposition",
    );
    if (disposition && /attachment/i.test(disposition.value || "")) {
      attachmentUrls.set(details.url, Date.now());
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders", "extraHeaders"],
);

chrome.webNavigation.onCommitted.addListener(details => {
  if (details.frameId === 0) tabMedia.delete(details.tabId);
});
chrome.tabs.onRemoved.addListener(tabId => {
  tabMedia.delete(tabId);
  forceTabs.delete(tabId);
  bypassTabs.delete(tabId);
});

function capturedFor(url) {
  const direct = requestsByUrl.get(url) || [];
  if (direct.length) return direct[direct.length - 1];
  const withoutHash = String(url).split("#")[0];
  for (const [candidate, entries] of requestsByUrl) {
    if (candidate.split("#")[0] === withoutHash && entries.length) {
      return entries[entries.length - 1];
    }
  }
  return null;
}

async function cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map(cookie => `${cookie.name}=${cookie.value}`).join("; ");
  } catch {
    return "";
  }
}

async function tabUrl(tabId) {
  if (tabId == null || tabId < 0) return "";
  try {
    return (await chrome.tabs.get(tabId))?.url || "";
  } catch {
    return "";
  }
}

async function buildEnvelope(url, item = {}, tab = null) {
  const captured = capturedFor(item.finalUrl || url) || capturedFor(url) || {};
  const tabId = captured.tabId ?? tab?.id ?? -1;
  const originalPage = item.referrer || tab?.url || await tabUrl(tabId);
  const headers = { ...(captured.headers || {}) };
  if (!Object.keys(headers).some(name => name.toLowerCase() === "cookie")) {
    const cookies = await cookieHeader(item.finalUrl || url);
    if (cookies) headers.Cookie = cookies;
  }
  return {
    url: captured.url || url,
    final_url: item.finalUrl || captured.final_url || url,
    original_page: originalPage || "",
    method: captured.method || "GET",
    headers,
    ...(captured.post_body ? { post_body: captured.post_body } : {}),
    ...(captured.capture_error ? { capture_error: captured.capture_error } : {}),
    suggested_filename: filenameOf(item.filename || ""),
    browser_profile: "chromium-mv3",
    captured_at: new Date().toISOString(),
  };
}

function filenameOf(value) {
  return String(value || "").split(/[\\/]/).pop().trim();
}

function extensionOf(url) {
  const clean = String(url || "").split(/[?#]/)[0];
  const name = clean.split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function localHostMode(url) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return "auto";
  }
  let best = "auto";
  let bestLength = -1;
  for (const [pattern, mode] of Object.entries(hostModes || {})) {
    const normal = String(pattern).toLowerCase().replace(/^\*\./, "");
    if ((host === normal || host.endsWith(`.${normal}`)) && normal.length > bestLength) {
      best = mode;
      bestLength = normal.length;
    }
  }
  return best;
}

async function hostMode(url) {
  const local = localHostMode(url);
  if (local !== "auto" || !isLocalServer()) return local;
  try {
    const response = await fetchWithTimeout(
      `${serverUrl}/api/browser/intercept-mode?url=${encodeURIComponent(url)}`,
      {},
      1500,
    );
    const data = await response.json();
    return data.mode || "auto";
  } catch {
    return "auto";
  }
}

function consumeTimed(map, key) {
  const timestamp = map.get(key);
  if (!timestamp || Date.now() - timestamp > CONTROL_TTL_MS) {
    map.delete(key);
    return false;
  }
  map.delete(key);
  return true;
}

function automaticIntercept(item, url) {
  if (attachmentUrls.has(url)) return true;
  const extension = extensionOf(url);
  if (WEB_ASSET_EXTENSIONS.has(extension)) return false;
  if (DOWNLOAD_EXTENSIONS.has(extension)) return true;
  return (item.fileSize || 0) >= 2 * 1024 * 1024;
}

async function shouldIntercept(item, url, captured) {
  if (!interceptEnabled || consumeTimed(bypassUrls, url)) return false;
  const tabId = captured?.tabId ?? -1;
  if (consumeTimed(bypassTabs, tabId)) return false;
  if (consumeTimed(forceTabs, tabId)) return true;
  const mode = await hostMode(url);
  if (mode === "always_browser") return false;
  if (mode === "always_lumi") return true;
  return automaticIntercept(item, url);
}

function cancelBrowserDownload(id) {
  return new Promise(resolve => {
    chrome.downloads.cancel(id, () => {
      chrome.downloads.erase({ id }, resolve);
    });
  });
}

async function refreshRepairPending() {
  if (!isLocalServer()) {
    repairPending = null;
    return null;
  }
  try {
    const response = await fetchWithTimeout(
      `${serverUrl}/api/browser/repair-pending`,
      {},
      1800,
    );
    repairPending = (await response.json()).pending || null;
  } catch {
    repairPending = null;
  }
  return repairPending;
}

async function repairWithEnvelope(envelope) {
  if (!isLocalServer()) throw new Error("Repair capture is restricted to local Lumi");
  if (envelope.capture_error) throw new Error(envelope.capture_error);
  const response = await fetchWithTimeout(`${serverUrl}/api/browser/repair-capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_envelope: envelope }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  repairPending = null;
  return data;
}

chrome.downloads.onCreated.addListener(item => void handleCreated(item));

async function handleCreated(item) {
  let url = item.finalUrl || item.url || "";
  if (!url || url.endsWith(".crx")) return;
  if (SKIP_PREFIXES.some(prefix => url.startsWith(prefix))) {
    if (!url.startsWith("blob:")) return;
    url = item.referrer || "";
    if (!url.startsWith("http")) return;
  }

  const captured = capturedFor(url);
  const pending = repairPending || await refreshRepairPending();
  if (pending) {
    const envelope = await buildEnvelope(url, item);
    if (envelope.capture_error) {
      notify("Lumi DM — Repair capture skipped", envelope.capture_error);
      return;
    }
    await cancelBrowserDownload(item.id);
    try {
      const result = await repairWithEnvelope(envelope);
      notify("Lumi DM — Link repaired", result.filename || pending.filename || url);
    } catch (error) {
      notify("Lumi DM — Repair failed", error.message);
    }
    return;
  }

  if (!(await shouldIntercept(item, url, captured))) return;
  if (isDuplicate(url)) {
    await cancelBrowserDownload(item.id);
    return;
  }
  if (!isLocalServer()) {
    notify("Lumi DM — Capture blocked", "Request secrets can only be sent to local Lumi.");
    return;
  }

  const envelope = await buildEnvelope(url, item);
  if (envelope.capture_error) {
    notify("Lumi DM — Browser kept download", envelope.capture_error);
    return;
  }
  await cancelBrowserDownload(item.id);
  const type = url.startsWith("magnet:") || extensionOf(url) === "torrent"
    ? "torrent"
    : "auto";
  await sendToServer(url, type, null, filenameOf(item.filename), envelope);
}

chrome.commands.onCommand.addListener(async command => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  if (command === "lumi-force-next") {
    forceTabs.set(tab.id, Date.now());
    notify("Lumi DM", "The next download in this tab will use Lumi.");
  } else if (command === "lumi-bypass-next") {
    bypassTabs.set(tab.id, Date.now());
    notify("Lumi DM", "The browser will handle the next download in this tab.");
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const menus = [
      ["LUMIDM-download-link", "Download with Lumi DM", ["link"]],
      ["LUMIDM-browser-link", "Download with browser", ["link"]],
      ["LUMIDM-repair-link", "Use link to repair Lumi download", ["link"]],
      ["LUMIDM-download-video", "Download video with Lumi DM", ["page", "frame"]],
      ["LUMIDM-grab-links", "Grab all download links from this page", ["page"]],
      ["LUMIDM-host-always", "Always use Lumi for this site", ["page"]],
      ["LUMIDM-host-browser", "Always use browser for this site", ["page"]],
      ["LUMIDM-host-auto", "Use automatic download rules for this site", ["page"]],
    ];
    for (const [id, title, contexts] of menus) {
      chrome.contextMenus.create({ id, title, contexts });
    }
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  void handleContextMenu(info, tab);
});

async function handleContextMenu(info, tab) {
  const id = info.menuItemId;
  if (id === "LUMIDM-download-link" && info.linkUrl) {
    await sendToServer(
      info.linkUrl,
      "auto",
      null,
      "",
      await buildEnvelope(info.linkUrl, {}, tab),
    );
    return;
  }
  if (id === "LUMIDM-browser-link" && info.linkUrl) {
    bypassUrls.set(info.linkUrl, Date.now());
    chrome.downloads.download({ url: info.linkUrl });
    return;
  }
  if (id === "LUMIDM-repair-link" && info.linkUrl) {
    try {
      const result = await repairWithEnvelope(await buildEnvelope(info.linkUrl, {}, tab));
      notify("Lumi DM — Link repaired", result.filename || info.linkUrl);
    } catch (error) {
      notify("Lumi DM — Repair failed", error.message);
    }
    return;
  }
  if (id === "LUMIDM-download-video" && tab?.url) {
    await sendToServer(tab.url, "video");
    return;
  }
  if (id === "LUMIDM-grab-links" && tab?.id) {
    await scanTabLinks(tab);
    return;
  }
  if (["LUMIDM-host-always", "LUMIDM-host-browser", "LUMIDM-host-auto"].includes(id)) {
    const mode = id === "LUMIDM-host-always"
      ? "always_lumi"
      : id === "LUMIDM-host-browser" ? "always_browser" : "auto";
    await setHostMode(tab?.url || info.pageUrl || "", mode);
  }
}

async function setHostMode(url, mode) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return;
  }
  hostModes = { ...hostModes };
  if (mode === "auto") delete hostModes[host];
  else hostModes[host] = mode;
  await chrome.storage.local.set({ hostModes });

  if (isLocalServer()) {
    try {
      await fetchWithTimeout(`${serverUrl}/api/host-profiles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: `browser-${host.replace(/[^a-z0-9]+/g, "-")}`,
          name: `Browser rule for ${host}`,
          host_pattern: host,
          intercept_mode: mode,
          enabled: mode !== "auto",
        }),
      });
    } catch {}
  }
  notify(
    "Lumi DM",
    mode === "auto" ? `Automatic rules restored for ${host}` : `Rule saved for ${host}`,
  );
}

async function scanTabLinks(tab) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const extensions = new Set([
          "zip", "rar", "7z", "gz", "tar", "bz2", "xz", "exe", "msi",
          "dmg", "pkg", "deb", "rpm", "apk", "ipa", "mp4", "mkv", "avi",
          "mov", "webm", "mp3", "flac", "wav", "aac", "ogg", "pdf", "epub",
          "torrent", "iso", "img",
        ]);
        const links = [];
        const seen = new Set();
        for (const anchor of document.querySelectorAll("a[href]")) {
          const href = anchor.href || "";
          if (!href.startsWith("http")) continue;
          const extension = href.split("?")[0].split(".").pop().toLowerCase();
          if (!extensions.has(extension) || seen.has(href)) continue;
          seen.add(href);
          links.push({
            url: href,
            filename: anchor.textContent.trim() || href.split("/").pop(),
            ext: extension,
          });
        }
        return links;
      },
    });
    await chrome.storage.local.set({
      grabResults: {
        url: tab.url,
        links: results?.[0]?.result || [],
        ts: Date.now(),
      },
    });
    await chrome.action.openPopup();
  } catch {
    chrome.tabs.create({ url: `${serverUrl}/?grab=${encodeURIComponent(tab.url)}` });
  }
}

chrome.webNavigation.onBeforeNavigate.addListener(details => {
  if (details.frameId !== 0) return;
  const url = details.url || "";
  if (!url.startsWith("magnet:") && extensionOf(url) !== "torrent") return;
  void sendToServer(url, "torrent");
  try {
    chrome.tabs.remove(details.tabId);
  } catch {}
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "DOWNLOAD") {
    void (async () => {
      const envelope = await buildEnvelope(message.url, {}, sender.tab || null);
      const result = await sendToServer(
        message.url,
        message.dlType || "auto",
        message.formatId || null,
        message.filename || "",
        envelope,
      );
      sendResponse({ ok: !result?.error, result });
    })();
    return true;
  }
  if (message.type === "GET_SNIFFED_MEDIA") {
    const tabId = message.tabId ?? sender.tab?.id ?? -1;
    sendResponse({ ok: true, urls: tabId >= 0 ? [...(tabMedia.get(tabId) || [])] : [] });
    return false;
  }
  if (message.type === "GET_VIDEO_FORMATS") {
    fetchWithTimeout(
      `${serverUrl}/api/downloads/video/formats?url=${encodeURIComponent(message.url)}`,
    )
      .then(response => response.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "GET_STATUS") {
    fetchWithTimeout(`${serverUrl}/api/downloads?limit=20`)
      .then(response => response.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "GET_REPAIR_PENDING") {
    void refreshRepairPending().then(pending => sendResponse({ ok: true, pending }));
    return true;
  }
  if (message.type === "SET_SERVER") {
    serverUrl = normaliseServer(message.server);
    chrome.storage.local.set({ server: serverUrl });
    sendResponse({ ok: true, localOnly: isLocalServer() });
    return false;
  }
  if (message.type === "SET_DIR") {
    downloadDir = message.dir || "";
    chrome.storage.local.set({ downloadDir });
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "SET_INTERCEPT") {
    interceptEnabled = message.enabled !== false;
    chrome.storage.local.set({ interceptEnabled });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

async function sendToServer(url, type, formatId = null, filename = "", envelope = null) {
  if (!isLocalServer()) {
    const error = "Secure request capture only supports the local Lumi server.";
    notify("Lumi DM — Capture blocked", error);
    return { error };
  }
  if (type === "torrent" || url.startsWith("magnet:") || extensionOf(url) === "torrent") {
    return postJson(`${serverUrl}/api/downloads/torrent`, {
      url,
      ...(downloadDir ? { target_dir: downloadDir } : {}),
    });
  }
  if (type === "video") {
    return postJson(`${serverUrl}/api/downloads/video`, {
      url,
      ...(formatId ? { format_id: formatId } : {}),
      ...(downloadDir ? { target_dir: downloadDir } : {}),
    });
  }

  const requestEnvelope = envelope || await buildEnvelope(url);
  if (requestEnvelope.capture_error) {
    const error = requestEnvelope.capture_error;
    notify("Lumi DM — Capture skipped", error);
    return { error };
  }
  return postJson(`${serverUrl}/api/downloads/start`, {
    url,
    filename: filename || requestEnvelope.suggested_filename || "",
    request_envelope: requestEnvelope,
    duplicate_policy: "reuse",
    ...(downloadDir ? { target_dir: downloadDir } : {}),
  });
}

async function postJson(endpoint, body) {
  try {
    const response = await fetchWithTimeout(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      notify("Lumi DM — Error", data.error || `HTTP ${response.status}`);
      return { error: data.error || `HTTP ${response.status}` };
    }
    notify(
      "Lumi DM — Started",
      data.filename || body.url?.slice(0, 80) || "Download queued",
    );
    return data;
  } catch (error) {
    notify("Lumi DM — Not reachable", `Cannot reach ${serverUrl} — is Lumi running?`);
    return { error: error.message };
  }
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title,
    message: String(message || "").slice(0, 240),
  });
}
