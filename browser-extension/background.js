/**
 * Lumi DM — Background Service Worker (MV3)
 */

const DEFAULT_SERVER = "http://localhost:7000";

// ── Keep service worker alive so we never miss a download ─────────────────────
// MV3 workers are killed after ~30s of inactivity; alarms prevent that.
chrome.alarms.create("LUMIDM-keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {
  chrome.storage.local.get("server"); // lightweight read keeps worker warm
});

// ── Cached settings (updated via storage.onChanged) ───────────────────────────

let _server           = DEFAULT_SERVER;
let _downloadDir      = "";
let _interceptEnabled = true;

chrome.storage.local.get(
  { server: DEFAULT_SERVER, downloadDir: "", interceptEnabled: true },
  (r) => {
    _server           = r.server.replace(/\/$/, "");
    _downloadDir      = r.downloadDir;
    _interceptEnabled = r.interceptEnabled;
  }
);

chrome.storage.onChanged.addListener((changes) => {
  if (changes.server)           _server           = changes.server.newValue.replace(/\/$/, "");
  if (changes.downloadDir)      _downloadDir      = changes.downloadDir.newValue;
  if (changes.interceptEnabled) _interceptEnabled = changes.interceptEnabled.newValue;
});

// ── URL deduplication (prevent double-sending same URL within 5s) ─────────────
const _recentUrls = new Map(); // url → timestamp
function _isDuplicate(url) {
  const now = Date.now();
  const last = _recentUrls.get(url);
  if (last && now - last < 5000) return true;
  _recentUrls.set(url, now);
  // Clean up entries older than 30s to avoid unbounded growth
  for (const [u, t] of _recentUrls) {
    if (now - t > 30000) _recentUrls.delete(u);
  }
  return false;
}

// ── Per-tab media URL sniffing ────────────────────────────────────────────────
// Intercepts real media requests so we can offer them for download on any site.

const _tabMedia = new Map(); // tabId → Set<url>

const _MEDIA_RE = /\.(m3u8|mpd|mp4|webm|mov|flv|ts|avi|mkv)(\?|#|$)/i;
const _MEDIA_TYPES = ["media", "xmlhttprequest", "other", "object"];

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    if (!_MEDIA_RE.test(details.url)) return;
    // Ignore tiny tracker pixels / very short URLs
    if (details.url.length < 20) return;
    let set = _tabMedia.get(details.tabId);
    if (!set) { set = new Set(); _tabMedia.set(details.tabId, set); }
    set.add(details.url);
  },
  { urls: ["<all_urls>"], types: _MEDIA_TYPES }
);

// Clear sniffed URLs on new navigation (new page = fresh state)
chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId === 0) _tabMedia.delete(details.tabId);
});

// Clean up closed tabs
chrome.tabs.onRemoved.addListener((tabId) => _tabMedia.delete(tabId));

// ── Context menus ─────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "LUMIDM-download-link",
    title: "Download with Lumi DM",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: "LUMIDM-download-video",
    title: "Download video (Lumi DM)",
    contexts: ["page", "frame"],
  });
  chrome.contextMenus.create({
    id: "LUMIDM-grab-links",
    title: "Grab all download links from this page",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "LUMIDM-download-link" && info.linkUrl)
    sendToServer(info.linkUrl, "auto");
  if (info.menuItemId === "LUMIDM-download-video" && tab?.url)
    sendToServer(tab.url, "video");
  if (info.menuItemId === "LUMIDM-grab-links" && tab?.id) {
    // Scan the page via content script, show results in extension popup
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const exts = new Set([
          "zip","rar","7z","gz","tar","bz2","xz","exe","msi","dmg","pkg",
          "deb","rpm","apk","ipa","mp4","mkv","avi","mov","webm","mp3","flac",
          "wav","aac","ogg","pdf","epub","torrent","iso","img",
        ]);
        const links = [], seen = new Set();
        document.querySelectorAll("a[href]").forEach(a => {
          const href = a.href || "";
          if (!href.startsWith("http")) return;
          const ext = href.split("?")[0].split(".").pop().toLowerCase();
          if (!exts.has(ext) || seen.has(href)) return;
          seen.add(href);
          links.push({ url: href, filename: a.textContent.trim() || href.split("/").pop(), ext });
        });
        return links;
      },
    }).then(results => {
      const links = results?.[0]?.result || [];
      chrome.storage.local.set({ grabResults: { url: tab.url, links, ts: Date.now() } });
      chrome.action.openPopup();
    }).catch(() => {
      // Fallback: open in app window
      chrome.tabs.create({ url: `${_server}/?grab=${encodeURIComponent(tab.url)}` });
    });
  }
});

// ── Early-warning: wake worker before browser creates the download item ───────
// Watches for Content-Disposition: attachment responses so the worker is already
// awake when onCreated fires — eliminates the race that causes missed downloads.
const _pendingAttachUrls = new Set();
chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0 || !_interceptEnabled) return;
    const cd = (details.responseHeaders || [])
      .find(h => h.name.toLowerCase() === "content-disposition");
    if (cd && /attachment/i.test(cd.value)) {
      _pendingAttachUrls.add(details.url);
      setTimeout(() => _pendingAttachUrls.delete(details.url), 10000);
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// ── Intercept browser downloads ───────────────────────────────────────────────

const _SKIP_PREFIXES = ["blob:", "data:", "chrome://", "chrome-extension://", "moz-extension://"];

// Extensions we actively want to intercept
const _DL_EXTS = new Set([
  "zip","rar","7z","gz","tar","bz2","xz","zst",
  "exe","msi","dmg","pkg","deb","rpm","apk","ipa","appx",
  "mp4","mkv","avi","mov","wmv","flv","webm","ts","m2ts",
  "mp3","flac","wav","aac","ogg","opus","m4a",
  "pdf","epub","mobi","azw3",
  "iso","img","bin","nrg",
  "torrent",
]);

// Extensions that are web assets — never intercept these
const _WEB_ASSET_EXTS = new Set([
  "png","jpg","jpeg","gif","webp","svg","ico","bmp","avif","tiff",
  "css","js","json","xml","html","htm","woff","woff2","ttf","eot",
  "map","ts", // ts here = TypeScript source, not transport stream
]);

function _shouldIntercept(item) {
  const url = item.url || item.finalUrl || "";
  // Already flagged as attachment by headers → always intercept
  if (_pendingAttachUrls.has(url)) return true;
  const path = url.split("?")[0].split("#")[0];
  const ext  = path.split(".").pop().toLowerCase();
  // Never intercept web asset types
  if (_WEB_ASSET_EXTS.has(ext)) return false;
  // Intercept known download extensions
  if (_DL_EXTS.has(ext)) return true;
  // Intercept if file size is known and large (> 2 MB) — likely intentional download
  if (item.fileSize > 0 && item.fileSize >= 2 * 1024 * 1024) return true;
  // Skip anything else (unknown type, small file) — let browser handle it
  return false;
}

// Track blob download tab URLs so we can re-fetch from the real source
const _blobTabUrls = new Map(); // tabId → page URL at time of blob download

chrome.downloads.onCreated.addListener((item) => {
  if (!_interceptEnabled) return;
  const url = item.url || item.finalUrl || "";
  if (_SKIP_PREFIXES.some(p => url.startsWith(p)) || url.endsWith(".crx")) return;

  // Blob URLs: use referrer (the real download link) if available
  if (url.startsWith("blob:")) {
    const realUrl = item.referrer || "";
    if (realUrl && realUrl.startsWith("http") && !_isDuplicate(realUrl) && _shouldIntercept({ ...item, url: realUrl })) {
      chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
      const filename = item.filename ? item.filename.split(/[\\/]/).pop().trim() : "";
      sendToServer(realUrl, "auto", null, filename);
    }
    return;
  }

  // Filter: only intercept downloads we actually care about
  if (!_shouldIntercept(item)) return;

  if (_isDuplicate(url)) {
    chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
    return;
  }

  chrome.downloads.cancel(item.id, () => {
    chrome.downloads.erase({ id: item.id });
  });

  const type     = (url.startsWith("magnet:") || url.endsWith(".torrent")) ? "torrent" : "auto";
  const filename = item.filename ? item.filename.split(/[\\/]/).pop().trim() : "";
  sendToServer(item.finalUrl || url, type, null, filename);
});

// ── Torrent / magnet navigation intercept ─────────────────────────────────────

chrome.webNavigation.onBeforeNavigate.addListener((details) => {
  if (details.frameId !== 0) return;
  const url = details.url || "";
  if (!url.startsWith("magnet:") && !url.endsWith(".torrent")) return;
  sendToServer(url, "torrent");
  try { chrome.tabs.remove(details.tabId); } catch (_) {}
});

// ── Messages ──────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

  if (msg.type === "DOWNLOAD") {
    sendToServer(msg.url, msg.dlType || "auto", msg.formatId || null).then(result => {
      sendResponse({ ok: true, result });
    });
    return true;
  }

  // Return sniffed media URLs — for the sender's tab, or a specific tabId
  if (msg.type === "GET_SNIFFED_MEDIA") {
    const tabId = msg.tabId ?? _sender.tab?.id ?? -1;
    const urls  = tabId >= 0 ? [...(_tabMedia.get(tabId) || [])] : [];
    sendResponse({ ok: true, urls });
    return false;
  }

  // Fetch available yt-dlp formats for a URL
  if (msg.type === "GET_VIDEO_FORMATS") {
    _fetch(`${_server}/api/downloads/video/formats?url=${encodeURIComponent(msg.url)}`)
      .then(r => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(e  => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (msg.type === "GET_STATUS") {
    _fetch(`${_server}/api/downloads?limit=20`)
      .then(r => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(e  => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (msg.type === "SET_SERVER") {
    _server = msg.server.replace(/\/$/, "");
    chrome.storage.local.set({ server: _server });
    sendResponse({ ok: true });
  }
  if (msg.type === "SET_DIR") {
    _downloadDir = msg.dir;
    chrome.storage.local.set({ downloadDir: _downloadDir });
    sendResponse({ ok: true });
  }
  if (msg.type === "SET_INTERCEPT") {
    _interceptEnabled = msg.enabled;
    chrome.storage.local.set({ interceptEnabled: _interceptEnabled });
    sendResponse({ ok: true });
  }
});

// ── Fetch with timeout (prevents toolbar freeze when server is unreachable) ────

function _fetch(url, opts = {}, timeout = 8000) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), timeout);
  return fetch(url, { ...opts, signal: ctrl.signal })
    .finally(() => clearTimeout(tid));
}

// ── Core API call ─────────────────────────────────────────────────────────────

async function sendToServer(url, type, formatId = null, filename = "") {
  // Video and torrent go directly (no staging dialog needed, yt-dlp resolves name)
  if (type === "torrent" || url.startsWith("magnet:") || url.endsWith(".torrent")) {
    return _postDirect(`${_server}/api/downloads/torrent`, { url, ..._downloadDir && { target_dir: _downloadDir } });
  }
  if (type === "video") {
    const body = { url, ..._downloadDir && { target_dir: _downloadDir } };
    if (formatId) body.format_id = formatId;
    return _postDirect(`${_server}/api/downloads/video`, body);
  }

  // HTTP downloads: stage first so user can confirm/change folder
  const body = { url, type: "http" };
  if (_downloadDir) body.target_dir = _downloadDir;
  if (filename)     body.filename   = filename;

  try {
    const r    = await _fetch(`${_server}/api/downloads/stage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.error) {
      chrome.notifications.create({
        type: "basic", iconUrl: "icons/icon48.png",
        title: "Lumi DM — Error", message: data.error,
      });
      return data;
    }
    return data;
  } catch (e) {
    chrome.notifications.create({
      type: "basic", iconUrl: "icons/icon48.png",
      title: "Lumi DM — Not reachable",
      message: `Cannot reach ${_server} — is the app running?`,
    });
    return { error: e.message };
  }
}

async function _postDirect(endpoint, body) {
  try {
    const r    = await _fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    chrome.notifications.create({
      type: "basic", iconUrl: "icons/icon48.png",
      title: data.error ? "Lumi DM — Error" : "Lumi DM — Started",
      message: data.error || data.filename || body.url?.slice(0, 80) || "",
    });
    return data;
  } catch (e) {
    chrome.notifications.create({
      type: "basic", iconUrl: "icons/icon48.png",
      title: "Lumi DM — Not reachable",
      message: `Cannot reach ${_server} — is the app running?`,
    });
    return { error: e.message };
  }
}
