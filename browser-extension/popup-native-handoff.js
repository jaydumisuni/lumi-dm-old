/* Lumi V5 popup behaviour — native desktop handoff and staged messaging. */
"use strict";

document.addEventListener("click", event => {
  const button = event.target.closest("#open-ui");
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  chrome.runtime.sendMessage({ type: "OPEN_DESKTOP" }, response => {
    if (!response?.ok) {
      const server = document.getElementById("server-input")?.value || "http://localhost:7000";
      chrome.tabs.create({ url: server });
    }
    window.close();
  });
}, true);

try {
  sendDownload = async function sendDownloadV5() {
    const input = document.getElementById("url-input");
    const url = (input?.value || "").trim();
    if (!url) return;
    let type = _selectedType;
    if (type === "auto") {
      if (url.startsWith("magnet:") || /\.torrent(?:\?|$)/i.test(url)) type = "torrent";
      else if (/\.(?:m3u8|mpd)(?:\?|$)/i.test(url)) type = "video";
      else type = "auto";
    }
    setMsg("Sending to Lumi's corner setup…");
    chrome.runtime.sendMessage({ type: "DOWNLOAD", url, dlType: type }, response => {
      if (response?.ok) {
        setMsg("Choose the name, folder and options in the Lumi corner popup.");
        if (input) input.value = "";
        setTimeout(loadList, 900);
      } else {
        setMsg(`Browser kept link: ${response?.result?.error || "Lumi is unavailable"}`);
      }
    });
  };
} catch (_) {}
