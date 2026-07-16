/**
 * Lumi DM — Content script
 *
 * - Intercepts .torrent / magnet clicks
 * - Scans page for downloadable links on request
 * - Floating video badge with quality picker on any video page / playing video
 *
 * Format resolution chain (tried in order):
 *   1. Sniffed network requests  (.m3u8 / .mpd / .mp4 / .webm …)
 *   2. DOM <video src> + <source> elements
 *   3. yt-dlp (for 1000+ platforms including YouTube)
 */
"use strict";

// ── Downloadable extensions ───────────────────────────────────────────────────

const _DL_EXTS = new Set([
  "zip","rar","7z","gz","tar","bz2","xz","exe","msi","dmg","pkg",
  "deb","rpm","apk","ipa","mp4","mkv","avi","mov","webm","mp3","flac",
  "wav","aac","ogg","pdf","epub","torrent","iso","img",
]);

// ── Torrent / magnet click intercept ─────────────────────────────────────────

document.addEventListener("click", e => {
  const link = e.target.closest("a[href]");
  if (!link) return;
  const href = link.href || "";
  if (href.endsWith(".torrent") || href.startsWith("magnet:")) {
    e.preventDefault();
    e.stopPropagation();
    chrome.runtime.sendMessage({ type: "DOWNLOAD", url: href, dlType: "torrent" });
  }
}, true);

// ── Scan links on request ─────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "SCAN_LINKS") {
    const links = [];
    const seen  = new Set();
    document.querySelectorAll("a[href]").forEach(a => {
      const href = a.href || "";
      if (!href.startsWith("http")) return;
      const ext = href.split("?")[0].split(".").pop().toLowerCase();
      if (!_DL_EXTS.has(ext)) return;
      if (seen.has(href)) return;
      seen.add(href);
      links.push({ url: href, filename: a.textContent.trim() || href.split("/").pop() || href, ext });
    });
    sendResponse({ links });
  }
});

// ── Video badge ───────────────────────────────────────────────────────────────

// Known video platform URL patterns (triggers badge even before a <video> plays)
const _VIDEO_URL_RE = /youtube\.com\/watch|youtu\.be\/[\w-]{6,}|vimeo\.com\/\d{5,}|dailymotion\.com\/video|twitch\.tv\/[^/]+\/(?:clip|v)\/|twitter\.com\/\w+\/status|x\.com\/\w+\/status|tiktok\.com\/@[^/]+\/video|reddit\.com\/r\/\w+\/comments|facebook\.com\/.*\/video|instagram\.com\/(?:p|reel|tv)\//i;

// Format cache: url → { ts, formats, title } | { ts, loading: true } | { ts, error }
const _cache    = new Map();
const CACHE_TTL = 5 * 60 * 1000; // 5 min

let _host       = null;
let _dismissed  = false;
let _lastUrl    = location.href;
let _pickerOpen = false;

// ── Format helpers ────────────────────────────────────────────────────────────

// Label a raw sniffed URL nicely
function _sniffLabel(url) {
  const u = url.split("?")[0].toLowerCase();
  if (u.endsWith(".m3u8"))          return { label: "HLS Stream",  dlType: "http" };
  if (u.endsWith(".mpd"))           return { label: "DASH Stream", dlType: "http" };
  if (u.includes("1080") || u.includes("1080p")) return { label: "1080p direct", dlType: "http" };
  if (u.includes("720")  || u.includes("720p"))  return { label: "720p direct",  dlType: "http" };
  if (u.includes("480")  || u.includes("480p"))  return { label: "480p direct",  dlType: "http" };
  if (u.includes("360")  || u.includes("360p"))  return { label: "360p direct",  dlType: "http" };
  const ext = u.split(".").pop();
  return { label: `Direct ${ext.toUpperCase()}`, dlType: "http" };
}

// Collect <video src> and <source> URLs from the DOM
function _domVideoSources() {
  const seen    = new Set();
  const results = [];
  document.querySelectorAll("video, video source").forEach(el => {
    const src = el.src || el.currentSrc || el.getAttribute("src") || "";
    if (!src || src.startsWith("blob:") || src.startsWith("data:")) return;
    if (seen.has(src)) return;
    seen.add(src);
    const info = _sniffLabel(src);
    results.push({ format_id: null, label: info.label, url: src, dlType: info.dlType, source: "dom" });
  });
  return results;
}

// Convert raw sniffed URL list → format objects
function _sniffedToFormats(urls) {
  const seen    = new Set();
  const results = [];
  // Prioritise HLS/DASH, then by URL length (longer = more specific)
  const sorted = [...urls].sort((a, b) => {
    const score = u => (u.includes(".m3u8") || u.includes(".mpd") ? 1 : 0);
    return score(b) - score(a) || b.length - a.length;
  });
  for (const url of sorted) {
    const key = url.split("?")[0];
    if (seen.has(key)) continue;
    seen.add(key);
    const info = _sniffLabel(url);
    results.push({ format_id: null, label: info.label, url, dlType: info.dlType, source: "sniff" });
    if (results.length >= 8) break; // cap list
  }
  return results;
}

// ── Format prefetch (called when badge appears) ───────────────────────────────

function prefetch(url) {
  const cached = _cache.get(url);
  if (cached && !cached.loading && Date.now() - cached.ts < CACHE_TTL) return; // already fresh
  if (cached?.loading) return; // already in flight

  _cache.set(url, { ts: Date.now(), loading: true });

  // Step 1: sniffed URLs (synchronous IPC, instant)
  chrome.runtime.sendMessage({ type: "GET_SNIFFED_MEDIA" }, res => {
    const sniffed = _sniffedToFormats(res?.urls || []);
    const dom     = _domVideoSources();
    const quick   = [...sniffed, ...dom];

    if (quick.length) {
      // We already have something useful — store it and start yt-dlp in parallel
      _cache.set(url, { ts: Date.now(), formats: quick, title: document.title, partial: true });
      _refreshPickerIfOpen(url);
    }

    // Step 2: yt-dlp (async, may take a few seconds) — always try for known platforms
    if (_VIDEO_URL_RE.test(url)) {
      chrome.runtime.sendMessage({ type: "GET_VIDEO_FORMATS", url }, ytRes => {
        const existing = _cache.get(url);
        if (ytRes?.ok && ytRes.data?.formats?.length) {
          // Merge: yt-dlp formats + any sniffed/dom we already had (dedup by label)
          const ytFmts  = ytRes.data.formats.map(f => ({ ...f, source: "ytdlp" }));
          const others  = (existing?.formats || []).filter(f => f.source !== "ytdlp");
          // Only keep sniffed/dom if they add something yt-dlp doesn't cover
          const merged  = [...ytFmts, ...others.filter(o => !ytFmts.some(y => y.label === o.label))];
          _cache.set(url, { ts: Date.now(), formats: merged, title: ytRes.data.title || document.title });
        } else if (!existing?.formats?.length) {
          // yt-dlp failed and we have nothing — store the error
          _cache.set(url, {
            ts: Date.now(),
            formats: quick,
            title: document.title,
            error: ytRes?.data?.error || "yt-dlp could not extract formats",
          });
        }
        _refreshPickerIfOpen(url);
      });
    } else if (!quick.length) {
      // Not a known platform and nothing sniffed yet — still try yt-dlp as a last resort
      chrome.runtime.sendMessage({ type: "GET_VIDEO_FORMATS", url }, ytRes => {
        if (ytRes?.ok && ytRes.data?.formats?.length) {
          _cache.set(url, { ts: Date.now(), formats: ytRes.data.formats.map(f => ({ ...f, source: "ytdlp" })), title: ytRes.data.title || document.title });
        } else {
          _cache.set(url, { ts: Date.now(), formats: [], title: document.title, error: "No downloadable video found on this page" });
        }
        _refreshPickerIfOpen(url);
      });
    }
  });
}

// If the picker is open and showing a loading spinner, update it with fresh data
function _refreshPickerIfOpen(url) {
  if (!_pickerOpen || !_host) return;
  const shadow = _host.shadowRoot;
  if (!shadow) return;
  const body   = shadow.getElementById("p-body");
  const title  = shadow.getElementById("p-title");
  const cached = _cache.get(url);
  if (body && cached && !cached.loading) {
    _renderFormats(body, title, cached, url);
  }
}

// ── Badge build ───────────────────────────────────────────────────────────────

function buildBadge() {
  if (_host) return;

  _host = document.createElement("div");
  _host.setAttribute("data-LUMIDM", "1");
  _host.style.cssText =
    "all:initial;position:fixed;z-index:2147483647;pointer-events:none;transition:top 200ms,left 200ms,bottom 200ms,right 200ms;";

  // Position near the playing/largest video element
  function _snapToVideo() {
    const videos = Array.from(document.querySelectorAll("video"))
      .filter(v => v.readyState >= 1 && v.offsetWidth > 100);
    const vid = videos.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight))[0];
    if (vid) {
      const r = vid.getBoundingClientRect();
      // Bottom-left corner of the video with small margin
      _host.style.bottom = "";
      _host.style.right  = "";
      _host.style.top    = (r.bottom - 54 + window.scrollY).toFixed(0) + "px";
      _host.style.left   = (r.left   + 12).toFixed(0) + "px";
    } else {
      // Fallback: screen bottom-left
      _host.style.top   = "";
      _host.style.left  = "22px";
      _host.style.bottom = "22px";
    }
  }
  _snapToVideo();
  const _posInterval = setInterval(() => { if (_host) _snapToVideo(); else clearInterval(_posInterval); }, 800);

  const shadow = _host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
:host{font-family:"Segoe UI",system-ui,sans-serif;font-size:12px;}
#badge{
  pointer-events:all;display:flex;align-items:center;gap:7px;
  background:rgba(17,19,23,0.82);border:1px solid rgba(255,255,255,0.13);
  border-radius:24px;padding:5px 9px 5px 6px;backdrop-filter:blur(6px);
  cursor:pointer;user-select:none;opacity:.72;white-space:nowrap;
  transition:opacity 140ms,background 140ms;
}
#badge:hover{opacity:1;background:rgba(17,19,23,0.96);}
.b-icon{width:20px;height:20px;border-radius:5px;object-fit:contain;flex-shrink:0;}
.b-label{color:#e8eaed;font-size:12px;font-weight:500;}
.b-dot{
  width:6px;height:6px;border-radius:50%;background:#4f9ef8;
  flex-shrink:0;animation:pulse 1.4s ease-in-out infinite;
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.b-close{
  width:17px;height:17px;border-radius:50%;border:none;
  background:rgba(255,255,255,0.09);color:rgba(255,255,255,0.45);
  font-size:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;
  pointer-events:all;transition:background 100ms,color 100ms;
}
.b-close:hover{background:rgba(248,113,113,.3);color:#f87171;}
#picker{
  pointer-events:all;position:absolute;bottom:44px;right:0;width:250px;
  background:rgba(17,19,23,.97);border:1px solid rgba(255,255,255,0.12);
  border-radius:12px;backdrop-filter:blur(10px);overflow:hidden;
  display:none;box-shadow:0 8px 32px rgba(0,0,0,.6);
  max-height:360px;
}
#picker.open{display:flex;flex-direction:column;}
.p-head{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 12px 8px;border-bottom:1px solid rgba(255,255,255,0.07);
  flex-shrink:0;
}
.p-title{
  color:#e8eaed;font-size:12px;font-weight:600;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;margin-right:8px;
}
.p-close{
  width:20px;height:20px;border-radius:50%;border:none;
  background:rgba(255,255,255,0.07);color:rgba(255,255,255,0.45);
  font-size:10px;cursor:pointer;display:flex;align-items:center;justify-content:center;
  flex-shrink:0;transition:background 100ms;
}
.p-close:hover{background:rgba(248,113,113,.3);color:#f87171;}
.p-body{padding:4px 0;overflow-y:auto;flex:1;min-height:0;}
.p-msg{padding:14px 12px;color:rgba(255,255,255,.38);font-size:11px;text-align:center;}
.p-err{padding:14px 12px;color:#f87171;font-size:11px;text-align:center;}
.p-section{
  padding:4px 12px 2px;font-size:10px;color:rgba(255,255,255,.3);
  text-transform:uppercase;letter-spacing:.06em;margin-top:2px;
}
.fmt{
  display:flex;align-items:center;gap:8px;width:100%;
  padding:8px 12px;background:none;border:none;
  color:#e8eaed;font-size:12px;cursor:pointer;text-align:left;
  transition:background 80ms;
}
.fmt:hover{background:rgba(79,158,248,.15);}
.fmt.needs-ff{color:rgba(255,255,255,.35);cursor:default;}
.fmt.needs-ff:hover{background:rgba(251,191,36,.06);}
.fmt strong{font-weight:600;}
.fmt-src{font-size:10px;color:rgba(255,255,255,.3);}
.fmt-ff{font-size:9px;color:#fbbf24;background:rgba(251,191,36,.12);
  border-radius:3px;padding:1px 4px;flex-shrink:0;}
.p-ffnote{padding:6px 12px 10px;font-size:10px;color:#fbbf24;
  border-top:1px solid rgba(255,255,255,.06);}
.spinner{
  display:inline-block;width:12px;height:12px;
  border:2px solid rgba(79,158,248,.2);border-top-color:#4f9ef8;
  border-radius:50%;animation:spin .7s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
<div id="picker">
  <div class="p-head">
    <span class="p-title" id="p-title">Loading…</span>
    <button class="p-close" id="p-close">✕</button>
  </div>
  <div class="p-body" id="p-body">
    <div class="p-msg"><span class="spinner"></span> Fetching formats…</div>
  </div>
</div>
<div id="badge">
  <img class="b-icon" id="b-icon" src="${chrome.runtime.getURL("icons/icon48.png")}" />
  <span class="b-dot" id="b-dot" style="display:none"></span>
  <span class="b-label" id="b-label">Download video</span>
  <button class="b-close" id="b-close" title="Dismiss">✕</button>
</div>`;

  const badge  = shadow.getElementById("badge");
  const bClose = shadow.getElementById("b-close");
  const picker = shadow.getElementById("picker");
  const pClose = shadow.getElementById("p-close");
  const pBody  = shadow.getElementById("p-body");
  const pTitle = shadow.getElementById("p-title");
  const bDot   = shadow.getElementById("b-dot");
  const bLabel = shadow.getElementById("b-label");

  // Hide icon if it fails to load (works in Shadow DOM unlike inline onerror)
  const bIcon  = shadow.getElementById("b-icon");
  if (bIcon) bIcon.addEventListener("error", () => {
    bIcon.style.cssText = "display:none;width:0;height:0;margin:0;padding:0;";
  });

  // Animate dot while prefetch is in-flight
  const dotCheck = setInterval(() => {
    if (!_host) { clearInterval(dotCheck); return; }
    const c = _cache.get(location.href);
    if (c?.loading) { bDot.style.display = ""; }
    else { bDot.style.display = "none"; clearInterval(dotCheck); }
  }, 400);

  bClose.addEventListener("click", e => {
    e.stopPropagation();
    _dismissed = true;
    removeBadge();
  });

  pClose.addEventListener("click", e => {
    e.stopPropagation();
    picker.classList.remove("open");
    _pickerOpen = false;
  });

  badge.addEventListener("click", e => {
    if (e.composedPath().includes(bClose)) return;
    if (_pickerOpen) {
      picker.classList.remove("open");
      _pickerOpen = false;
      return;
    }
    _pickerOpen = true;
    picker.classList.add("open");

    // Flip picker so it stays fully within the viewport
    requestAnimationFrame(() => {
      const hostRect = _host.getBoundingClientRect();
      const pickerH  = picker.offsetHeight || 300;
      const pickerW  = 250; // matches CSS width

      // Vertical: open downward if too close to the top
      if (hostRect.top < pickerH + 60) {
        picker.style.bottom = "auto";
        picker.style.top    = "44px";
      } else {
        picker.style.bottom = "44px";
        picker.style.top    = "auto";
      }

      // Horizontal: right-align by default; flip to left-align if it would clip the left edge
      if (hostRect.right - pickerW < 8) {
        picker.style.right = "auto";
        picker.style.left  = "0";
      } else {
        picker.style.right = "0";
        picker.style.left  = "auto";
      }
    });

    const url    = location.href;
    const cached = _cache.get(url);

    if (cached && !cached.loading && Date.now() - cached.ts < CACHE_TTL) {
      _renderFormats(pBody, pTitle, cached, url);
    } else {
      pTitle.textContent = "Loading…";
      pBody.innerHTML    = '<div class="p-msg"><span class="spinner"></span> Fetching formats…</div>';
      // prefetch may already be running; _refreshPickerIfOpen will update us
      if (!cached?.loading) prefetch(url);
    }
  });

  function _renderFormats(body, titleEl, data, url) {
    titleEl.textContent = data.title || "Select quality";
    const fmts = data.formats || [];

    if (!fmts.length) {
      body.innerHTML = `<div class="p-err">${esc(data.error || "No downloadable video found")}</div>`;
      return;
    }

    // Group by source
    const ytFmts     = fmts.filter(f => f.source === "ytdlp");
    const sniffFmts  = fmts.filter(f => f.source === "sniff");
    const domFmts    = fmts.filter(f => f.source === "dom");

    let html = "";
    const hasNeedsFF = fmts.some(f => f.needs_ffmpeg);

    const renderGroup = (label, list) => {
      if (!list.length) return;
      html += `<div class="p-section">${label}</div>`;
      list.forEach(f => {
        const dataUrl  = esc(f.url || url);
        const dataFmt  = esc(f.format_id || "");
        const dataTyp  = esc(f.dlType || (f.source === "ytdlp" ? "video" : "http"));
        const needsFF  = f.needs_ffmpeg;
        const cls      = needsFF ? "fmt needs-ff" : "fmt";
        const ffBadge  = needsFF ? `<span class="fmt-ff">needs ffmpeg</span>` : "";
        const srcBadge = (!needsFF && f.source !== "ytdlp") ? ` <span class="fmt-src">${esc(f.source)}</span>` : "";
        html += `<button class="${cls}" data-url="${dataUrl}" data-fmt="${dataFmt}" data-type="${dataTyp}" data-needs-ff="${needsFF ? 1 : 0}">` +
                `⬇ <strong>${esc(f.label)}</strong>${srcBadge}${ffBadge}` +
                `</button>`;
      });
    };

    renderGroup("Qualities", ytFmts);
    renderGroup("Captured streams", sniffFmts);
    renderGroup("Direct sources", domFmts);

    if (hasNeedsFF) {
      html += `<div class="p-ffnote">⚠ Higher qualities require ffmpeg — reinstall the app to get it</div>`;
    }
    if (data.error && fmts.length) {
      html += `<div class="p-msg" style="font-size:10px;padding:6px 12px">${esc(data.error)}</div>`;
    }

    body.innerHTML = html;
    body.querySelectorAll(".fmt").forEach(btn => {
      btn.addEventListener("click", () => {
        if (btn.dataset.needsFf === "1") {
          pBody.innerHTML += `<div class="p-ffnote" style="color:#f87171">ffmpeg not found — reinstall Lumi DM to restore it</div>`;
          return;
        }
        const dlUrl  = btn.dataset.url;
        const dlType = btn.dataset.type;
        const fmtId  = btn.dataset.fmt || null;
        chrome.runtime.sendMessage({ type: "DOWNLOAD", url: dlUrl, dlType, formatId: fmtId || undefined });
        picker.classList.remove("open");
        _pickerOpen = false;
        bLabel.textContent = "Starting…";
        setTimeout(() => { if (bLabel) bLabel.textContent = "Download video"; }, 2500);
      });
    });
  }

  // Expose so prefetch can call it
  shadow._renderFormats = _renderFormats;
  document.documentElement.appendChild(_host);
}

// ── Picker refresh helper (used by prefetch after cache is updated) ───────────

function _refreshPickerIfOpen(url) {
  if (!_pickerOpen || !_host) return;
  const shadow = _host.shadowRoot;
  if (!shadow) return;
  const body   = shadow.getElementById("p-body");
  const title  = shadow.getElementById("p-title");
  const cached = _cache.get(url);
  if (body && title && cached && !cached.loading) {
    shadow._renderFormats(body, title, cached, url);
  }
}

function removeBadge() {
  if (_host) { _host.remove(); _host = null; _pickerOpen = false; }
}

function esc(v) {
  return String(v ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ── Badge lifecycle ───────────────────────────────────────────────────────────

function isVideoPage() {
  if (_VIDEO_URL_RE.test(location.href)) return true;
  return Array.from(document.querySelectorAll("video")).some(v => v.readyState >= 1 && !v.paused);
}

function checkBadge() {
  if (_dismissed) return;
  if (isVideoPage()) {
    buildBadge();
    prefetch(location.href); // start format discovery immediately
  } else if (_host) {
    removeBadge();
  }
}

// SPA navigation watcher (YouTube etc. change URL without full page reload)
setInterval(() => {
  if (location.href !== _lastUrl) {
    _lastUrl   = location.href;
    _dismissed = false;
    removeBadge();
    setTimeout(checkBadge, 1200);
  }
}, 800);

// Show badge the moment any <video> starts playing on any page
document.addEventListener("play", () => {
  if (!_dismissed && !_host) checkBadge();
}, true);

// Initial check — delay lets SPA frameworks finish rendering
setTimeout(checkBadge, 1500);
