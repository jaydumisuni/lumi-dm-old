/**
 * Lumi DM v2 — browser capture and repair bridge (Manifest V3).
 */

const DEFAULT_SERVER = "http://localhost:7000";
const REQUEST_TTL_MS = 2 * 60 * 1000;
const CONTROL_TTL_MS = 30 * 1000;
const KEEPALIVE_MINUTES = 0.5;

let _server = DEFAULT_SERVER;
let _downloadDir = "";
let _interceptEnabled = true;
let _hostModes = {};
let _repairPending = null;

const _recentUrls = new Map();
const _tabMedia = new Map();
const _requestById = new Map();
const _requestByUrl = new Map();
const _pendingAttachUrls = new Map();
const _forceTabs = new Map();
const _bypassTabs = new Map();
const _bypassUrls = new Map();

const _MEDIA_RE = /\.(m3u8|mpd|mp4|webm|mov|flv|ts|avi|mkv)(\?|#|$)/i;
const _MEDIA_TYPES = ["media", "xmlhttprequest", "other", "object"];
const _SKIP_PREFIXES = [
  "blob:", "data:", "chrome://", "chrome-extension://", "moz-extension://",
];
const _DL_EXTS = new Set([
  "zip","rar","7z","gz","tar","bz2","xz","zst",
  "exe","msi","dmg","pkg","deb","rpm","apk","ipa","appx",
  "mp4","mkv","avi","mov","wmv","flv","webm","ts","m2ts",
  "mp3","flac","wav","aac","ogg","opus","m4a",
  "pdf","epub","mobi","azw3","iso","img","bin","nrg","torrent",
]);
const _WEB_ASSET_EXTS = new Set([
  "png","jpg","jpeg","gif","webp","svg","ico","bmp","avif","tiff",
  "css","js","json","xml","html","htm","woff","woff2","ttf","eot","map",
]);
const _CAPTURE_HEADERS = new Set([
  "accept", "accept-language", "authorization", "content-type", "cookie",
  "origin", "proxy-authorization", "referer", "user-agent",
]);

chrome.alarms.create("LUMIDM-keepalive", { periodInMinutes: KEEPALIVE_MINUTES });
chrome.alarms.onAlarm.addListener(() => {
  chrome.storage.local.get("server");
  _cleanup();
  void _refreshRepairPending();
});

chrome.storage.local.get(
  {
    server: DEFAULT_SERVER,
    downloadDir: "",
    interceptEnabled: true,
    hostModes: {},
  },
  values => {
    _server = _normaliseServer(values.server);
    _downloadDir = values.downloadDir || "";
    _interceptEnabled = values.interceptEnabled !== false;
    _hostModes = values.hostModes || {};
    void _refreshRepairPending();
  },
);

chrome.storage.onChanged.addListener(changes => {
  if (changes.server) _server = _normaliseServer(changes.server.newValue);
  if (changes.downloadDir) _downloadDir = changes.downloadDir.newValue || "";
  if (changes.interceptEnabled) {
    _interceptEnabled = changes.interceptEnabled.newValue !== false;
  }
  if (changes.hostModes) _hostModes = changes.hostModes.newValue || {};
});

function _normaliseServer(value) {
  return String(value || DEFAULT_SERVER).replace(/\/$/, "");
}

function _isLocalServer() {
  try {
    const host = new URL(_server).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1";
  } catch {
    return false;
  }
}

function _fetch(url, options = {}, timeout = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  return fetch(url, { ...options, signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

function _cleanup() {
  const now = Date.now();
  for (const map of [
    _recentUrls, _pendingAttachUrls, _forceTabs, _bypassTabs, _bypassUrls,
  ]) {
    for (const [key, value] of map) {
      const timestamp = typeof value === "number" ? value : value?.timestamp || 0;
      if (now - timestamp > REQUEST_TTL_MS) map.delete(key);
    }
  }
  for (const [id, value] of _requestById) {
    if (now - value.timestamp > REQUEST_TTL_MS) _requestById.delete(id);
  }
  for (const [url, values] of _requestByUrl) {
    const fresh = values.filter(value => now - value.timestamp <= REQUEST_TTL_MS);
    if (fresh.length) _requestByUrl.set(url, fresh.slice(-8));
    else _requestByUrl.delete(url);
  }
}

function _isDuplicate(url) {
  const now = Date.now();
  const previous = _recentUrls.get(url);
  if (previous && now - previous < 5000) return true;
  _recentUrls.set(url, now);
  return false;
}

function _headerObject(headers = []) {
  const result = {};
  for (const header of headers) {
    const name = String(header.name || "").toLowerCase();
    if (!_CAPTURE_HEADERS.has(name) || header.value == null) continue;
    const canonical = name.split("-")
      .map(part => part ? part[0].toUpperCase() + part.slice(1) : part)
      .join("-");
    result[canonical] = String(header.value);
  }
  return result;
}

function _bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const stride = 0x8000;
  for (let index = 0; index < bytes.length; index += stride) {
    binary += String.fromCharCode(...bytes.subarray(index, index + stride));
  }
  return btoa(binary);
}

function _requestBody(details) {
  const body = details.requestBody;
  if (!body) return null;
  if (body.formData) return { kind: "form", data: body.formData };
  const chunks = [];
  for (const item of body.raw || []) {
    if (item.bytes) chunks.push(_bytesToBase64(item.bytes));
  }
  if (!chunks.length) return null;
  return { kind: "base64", data: chunks.join("") };
}

function _rememberRequest(details) {
  const current = _requestById.get(details.requestId) || {
    url: details.url,
    final_url: details.url,
    method: details.method || "GET",
    headers: {},
    post_body: null,
    tabId: details.tabId,
    frameId: details.frameId,
    timestamp: Date.now(),
  };
  current.url = current.url || details.url;
  current.final_url = details.url;
  current.method = details.method || current.method || "GET";
  current.tabId = details.tabId;
  current.frameId = details.frameId;
  current.timestamp = Date.now();
  const body = _requestBody(details);
  if (body) current.post_body = body;
  _requestById.set(details.requestId, current);
  const bucket = _requestByUrl.get(details.url) || [];
  bucket.push(current);
  _requestByUrl.set(details.url, bucket.slice(-8));
  return current;
}

chrome.webRequest.onBeforeRequest.addListener(
  details => {
    const current = _rememberRequest(details);
    if (details.tabId >= 0 && _MEDIA_RE.test(details.url) && details.url.length >= 20) {
      let media = _tabMedia.get(details.tabId);
      if (!media) {
        media = new Set();
        _tabMedia.set(details.tabId, media);
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
    const current = _requestById.get(details.requestId) || _rememberRequest(details);
    current.headers = { ...current.headers, ..._headerObject(details.requestHeaders) };
    current.timestamp = Date.now();
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onBeforeRedirect.addListener(
  details => {
    const current = _requestById.get(details.requestId);
    if (!current) return;
    current.final_url = details.redirectUrl;
    current.timestamp = Date.now();
    const bucket = _requestByUrl.get(details.redirectUrl) || [];
    bucket.push(current);
    _requestByUrl.set(details.redirectUrl, bucket.slice(-8));
  },
  { urls: ["<all_urls>"] },
);

chrome.webRequest.onHeadersReceived.addListener(
  details => {
    const disposition = (details.responseHeaders || [])
      .find(header => String(header.name).toLowerCase() === "content-disposition");
    if (disposition && /attachment/i.test(disposition.value || "")) {
      _pendingAttachUrls.set(details.url, Date.now());
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders", "extraHeaders"],
);

chrome.webNavigation.onCommitted.addListener(details => {
  if (details.frameId === 0) _tabMedia.delete(details.tabId);
});
chrome.tabs.onRemoved.addListener(tabId => {
  _tabMedia.delete(tabId);
  _forceTabs.delete(tabId);
  _bypassTabs.delete(tabId);
});

function _capturedFor(url) {
  const direct = _requestByUrl.get(url) || [];
  if (direct.length) return direct[direct.length - 1];
  const withoutHash = String(url).split("#", 1)[0];
  for (const [candidate, entries] of _requestByUrl) {
    if (candidate.split("#", 1)[0] === withoutHash && entries.length) {
      return entries[entries.length - 1];
    }
  }
  return null;
}

async function _cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map(cookie => `${cookie.name}=${cookie.value}`).join("; ");
  } catch {
    return "";
  }
}

async function _tabUrl(tabId) {
  if (tabId == null || tabId < 0) return "";
  try {
    return (await chrome.tabs.get(tabId))?.url || "";
  } catch {
    return "";
  }
}

async function _buildEnvelope(url, item = {}, tab = null) {
  const captured = _capturedFor(item.finalUrl || url) || _capturedFor(url) || {};
  const tabId = captured.tabId ?? tab?.id ?? -1;
  const originalPage = item.referrer || tab?.url || await _tabUrl(tabId);
  const headers = { ...(captured.headers || {}) };
  if (!Object.keys(headers).some(name => name.toLowerCase() === "cookie")) {
    const cookies = await _cookieHeader(item.finalUrl || url);
    if (cookies) headers.Cookie = cookies;
  }
  return {
    url: captured.url || url,
    final_url: item.finalUrl || captured.final_url || url,
    original_page: originalPage || "",
    method: captured.method || "GET",
    headers,
    ...(captured.post_body ? { post_body: captured.post_body } : {}),
    suggested_filename: _filename(item.filename || ""),
    browser_profile: "chromium-mv3",
    captured_at: new Date().toISOString(),
  };
}

function _filename(value) {
  return String(value || "").split(/[\\/]/).pop().trim();
}

function _extension(url) {
  const clean = String(url || "").split(/[?#]/, 1)[0];
  const name = clean.split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function _localHostMode(url) {
  let host = "";
  try { host = new URL(url).hostname.toLowerCase(); } catch { return "auto"; }
  let best = "auto";
  let bestLength = -1;
  for (const [pattern, mode] of Object.entries(_hostModes || {})) {
    const normal = String(pattern).toLowerCase().replace(/^\*\./, "");
    if ((host === normal || host.endsWith(`.${normal}`)) && normal.length > bestLength) {
      best = mode;
      bestLength = normal.length;
    }
  }
  return best;
}

async function _hostMode(url) {
  const local = _localHostMode(url);
  if (local !== "auto") return local;
  if (!_isLocalServer()) return "auto";
  try {
    const response = await _fetch(
      `${_server}/api/browser/intercept-mode?url=${encodeURIComponent(url)}`,
      {},
      1500,
    );
    const data = await response.json();
    return data.mode || "auto";
  } catch {
    return "auto";
  }
}

function _consumeTimed(map, key) {
  const timestamp = map.get(key);
  if (!timestamp || Date.now() - timestamp > CONTROL_TTL_MS) {
    map.delete(key);
    return false;
  }
  map.delete(key);
  return true;
}

function _automaticIntercept(item, url) {
  if (_pendingAttachUrls.has(url)) return true;
  const extension = _extension(url);
  if (_WEB_ASSET_EXTS.has(extension)) return false;
  if (_DL_EXTS.has(extension)) return true;
  if ((item.fileSize || 0) >= 2 * 1024 * 1024) return true;
  return false;
}

async function _shouldIntercept(item, url, captured) {
  if (!_interceptEnabled) return false;
  if (_consumeTimed(_bypassUrls, url)) return false;
  const tabId = captured?.tabId ?? -1;
  if (_consumeTimed(_bypassTabs, tabId)) return false;
  if (_consumeTimed(_forceTabs, tabId)) return true;
  const mode = await _hostMode(url);
  if (mode === "always_browser") return false;
  if (mode === "always_lumi") return true;
  return _automaticIntercept(item, url);
}

function _cancelBrowserDownload(id) {
  return new Promise(resolve => {
    chrome.downloads.cancel(id, () => {
      chrome.downloads.erase({ id }, () => resolve());
    });
  });
}

async function _refreshRepairPending() {
  if (!_isLocalServer()) {
    _repairPending = null;
    return null;
  }
  try {
    const response = await _fetch(`${_server}/api/browser/repair-pending`, {}, 1800);
    const data = await response.json();
    _repairPending = data.pending || null;
  } catch {
    _repairPending = null;
  }
  return _repairPending;
}

async function _repairWithEnvelope(envelope) {
  if (!_isLocalServer()) throw new Error("Repair capture is restricted to local Lumi");
  const response = await _fetch(`${_server}/api/browser/repair-capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_envelope: envelope }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  _repairPending = null;
  return data;
}

chrome.downloads.onCreated.addListener(item => {
  void _handleCreated(item);
});

async function _handleCreated(item) {
  let url = item.finalUrl || item.url || "";
  if (!url || url.endsWith(".crx")) return;
  if (_SKIP_PREFIXES.some(prefix => url.startsWith(prefix))) {
    if (!url.startsWith("blob:")) return;
    url = item.referrer || "";
    if (!url.startsWith("http")) return;
  }

  const captured = _capturedFor(url);
  const repair = _repairPending || await _refreshRepairPending();
  if (repair) {
    const envelope = await _buildEnvelope(url, item);
    await _cancelBrowserDownload(item.id);
    try {
      const result = await _repairWithEnvelope(envelope);
      _notify("Lumi DM — Link repaired", result.filename || repair.filename || url);
    } catch (error) {
      _notify("Lumi DM — Repair failed", error.message);
    }
    return;
  }

  if (!(await _shouldIntercept(item, url, captured))) return;
  if (_isDuplicate(url)) {
    await _cancelBrowserDownload(item.id);
    return;
  }
  if (!_isLocalServer()) {
    _notify("Lumi DM — Capture blocked", "Request secrets can only be sent to local Lumi.");
    return;
  }

  await _cancelBrowserDownload(item.id);
  const envelope = await _buildEnvelope(url, item);
  const type = url.startsWith("magnet:") || _extension(url) === "torrent"
    ? "torrent"
    : "auto";
  await sendToServer(url, type, null, _filename(item.filename), envelope);
}

chrome.commands.onCommand.addListener(async command => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  if (command === "lumi-force-next") {
    _forceTabs.set(tab.id, Date.now());
    _notify("Lumi DM", "The next download in this tab will use Lumi.");
  } else if (command === "lumi-bypass-next") {
    _bypassTabs.set(tab.id, Date.now());
    _notify("Lumi DM", "The browser will handle the next download in this tab.");
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
  void _handleContextMenu(info, tab);
});

async function _handleContextMenu(info, tab) {
  const id = info.menuItemId;
  if (id === "LUMIDM-download-link" && info.linkUrl) {
    const envelope = await _buildEnvelope(info.linkUrl, {}, tab);
    await sendToServer(info.linkUrl, "auto", null, "", envelope);
    return;
  }
  if (id === "LUMIDM-browser-link" && info.linkUrl) {
    _bypassUrls.set(info.linkUrl, Date.now());
    chrome.downloads.download({ url: info.linkUrl });
    return;
  }
  if (id === "LUMIDM-repair-link" && info.linkUrl) {
    try {
      const envelope = await _buildEnvelope(info.linkUrl, {}, tab);
      const result = await _repairWithEnvelope(envelope);
      _notify("Lumi DM — Link repaired", result.filename || info.linkUrl);
    } catch (error) {
      _notify("Lumi DM — Repair failed", error.message);
    }
    return;
  }
  if (id === "LUMIDM-download-video" && tab?.url) {
    await sendToServer(tab.url, "video");
    return;
  }
  if (id === "LUMIDM-grab-links" && tab?.id) {
    await _scanTabLinks(tab);
    return;
  }
  if (["LUMIDM-host-always", "LUMIDM-host-browser", "LUMIDM-host-auto"].includes(id)) {
    const mode = id === "LUMIDM-host-always"
      ? "always_lumi"
      : id === "LUMIDM-host-browser" ? "always_browser" : "auto";
    await _setHostMode(tab?.url || info.pageUrl || "", mode);
  }
}

async function _setHostMode(url, mode) {
  let host;
  try { host = new URL(url).hostname.toLowerCase(); } catch { return; }
  _hostModes = { ..._hostModes };
  if (mode === "auto") delete _hostModes[host];
  else _hostModes[host] = mode;
  await chrome.storage.local.set({ hostModes: _hostModes });

  if (_isLocalServer()) {
    try {
      await _fetch(`${_server}/api/host-profiles`, {
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
  _notify("Lumi DM", mode === "auto" ? `Automatic rules restored for ${host}` : `Rule saved for ${host}`);
}

async function _scanTabLinks(tab) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const extensions = new Set([
          "zip","rar","7z","gz","tar","bz2","xz","exe","msi","dmg","pkg",
          "deb","rpm","apk","ipa","mp4","mkv","avi","mov","webm","mp3","flac",
          "wav","aac","ogg","pdf","epub","torrent","iso","img",
        ]);
        const links = [];
        const seen = new Set();
        document.querySelectorAll("a[href]").forEach(anchor => {
          const href = anchor.href || "";
          if (!href.startsWith("http")) return;
          const extension = href.split("?")[0].split(".").pop().toLowerCase();
          if (!extensions.has(extension) || seen.has(href)) return;
          seen.add(href);
          links.push({
            url: href,
            filename: anchor.textContent.trim() || href.split("/").pop(),
            ext: extension,
          });
        });
        return links;
      },
    });
    const links = results?.[0]?.result || [];
    await chrome.storage.local.set({
      grabResults: { url: tab.url, links, ts: Date.now() },
    });
    await chrome.action.openPopup();
  } catch {
    chrome.tabs.create({ url: `${_server}/?grab=${encodeURIComponent(tab.url)}` });
  }
}

chrome.webNavigation.onBeforeNavigate.addListener(details => {
  if (details.frameId !== 0) return;
  const url = details.url || "";
  if (!url.startsWith("magnet:") && _extension(url) !== "torrent") return;
  void sendToServer(url, "torrent");
  try { chrome.tabs.remove(details.tabId); } catch {}
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "DOWNLOAD") {
    void (async () => {
      const envelope = await _buildEnvelope(message.url, {}, sender.tab || null);
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
    sendResponse({ ok: true, urls: tabId >= 0 ? [...(_tabMedia.get(tabId) || [])] : [] });
    return false;
  }
  if (message.type === "GET_VIDEO_FORMATS") {
    _fetch(`${_server}/api/downloads/video/formats?url=${encodeURIComponent(message.url)}`)
      .then(response => response.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "GET_STATUS") {
    _fetch(`${_server}/api/downloads?limit=20`)
      .then(response => response.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "GET_REPAIR_PENDING") {
    void _refreshRepairPending().then(pending => sendResponse({ ok: true, pending }));
    return true;
  }
  if (message.type === "SET_SERVER") {
    _server = _normaliseServer(message.server);
    chrome.storage.local.set({ server: _server });
    sendResponse({ ok: true, localOnly: _isLocalServer() });
    return false;
  }
  if (message.type === "SET_DIR") {
    _downloadDir = message.dir || "";
    chrome.storage.local.set({ downloadDir: _downloadDir });
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "SET_INTERCEPT") {
    _interceptEnabled = message.enabled !== false;
    chrome.storage.local.set({ interceptEnabled: _interceptEnabled });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

async function sendToServer(url, type, formatId = null, filename = "", envelope = null) {
  if (!_isLocalServer()) {
    const error = "Secure request capture only supports the local Lumi server.";
    _notify("Lumi DM — Capture blocked", error);
    return { error };
  }
  if (type === "torrent" || url.startsWith("magnet:") || _extension(url) === "torrent") {
    return _postDirect(`${_server}/api/downloads/torrent`, {
      url,
      ...(_downloadDir ? { target_dir: _downloadDir } : {}),
    });
  }
  if (type === "video") {
    return _postDirect(`${_server}/api/downloads/video`, {
      url,
      ...(formatId ? { format_id: formatId } : {}),
      ...(_downloadDir ? { target_dir: _downloadDir } : {}),
    });
  }

  const requestEnvelope = envelope || await _buildEnvelope(url);
  return _postDirect(`${_server}/api/downloads/start`, {
    url,
    filename: filename || requestEnvelope.suggested_filename || "",
    request_envelope: requestEnvelope,
    duplicate_policy: "reuse",
    ...(_downloadDir ? { target_dir: _downloadDir } : {}),
  });
}

async function _postDirect(endpoint, body) {
  try {
    const response = await _fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      _notify("Lumi DM — Error", data.error || `HTTP ${response.status}`);
      return { error: data.error || `HTTP ${response.status}` };
    }
    _notify("Lumi DM — Started", data.filename || body.url?.slice(0, 80) || "Download queued");
    return data;
  } catch (error) {
    _notify("Lumi DM — Not reachable", `Cannot reach ${_server} — is Lumi running?`);
    return { error: error.message };
  }
}

function _notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title,
    message: String(message || "").slice(0, 240),
  });
}
