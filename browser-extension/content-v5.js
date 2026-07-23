/* Lumi V5 page-side hardening layered over the existing video picker. */
"use strict";

let lumiV5InterceptEnabled = true;
chrome.storage.local.get({ interceptEnabled: true }, value => {
  lumiV5InterceptEnabled = value.interceptEnabled !== false;
});
chrome.storage.onChanged.addListener(changes => {
  if (changes.interceptEnabled) {
    lumiV5InterceptEnabled = changes.interceptEnabled.newValue !== false;
  }
});

// Runs at window capture before the older document listener. When interception is
// disabled it stops Lumi's listener without cancelling the browser's default click.
window.addEventListener("click", event => {
  if (lumiV5InterceptEnabled) return;
  const link = event.target?.closest?.("a[href]");
  const href = link?.href || "";
  if (href.startsWith("magnet:") || /\.torrent(?:\?|#|$)/i.test(href)) {
    event.stopImmediatePropagation();
  }
}, true);

function anchorLumiBadgeToViewportVideo() {
  const host = document.querySelector('[data-LUMIDM="1"]');
  if (!host) return;
  const videos = [...document.querySelectorAll("video")]
    .filter(video => video.readyState >= 1 && video.offsetWidth > 100)
    .sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight);
  const video = videos[0];
  if (!video) return;
  const bounds = video.getBoundingClientRect();
  host.style.position = "fixed";
  host.style.bottom = "";
  host.style.right = "";
  host.style.top = `${Math.max(8, Math.min(window.innerHeight - 58, bounds.bottom - 54))}px`;
  host.style.left = `${Math.max(8, Math.min(window.innerWidth - 230, bounds.left + 12))}px`;
}

window.addEventListener("scroll", anchorLumiBadgeToViewportVideo, { passive: true });
window.addEventListener("resize", anchorLumiBadgeToViewportVideo, { passive: true });
setInterval(anchorLumiBadgeToViewportVideo, 300);
