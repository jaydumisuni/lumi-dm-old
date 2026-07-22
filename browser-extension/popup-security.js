/* Authenticated popup transport and one-time pairing UI. */
"use strict";

const _nativePopupFetch = globalThis.fetch.bind(globalThis);

async function _popupAuthToken() {
  const values = await chrome.storage.local.get({ apiToken: "" });
  return String(values.apiToken || "");
}

function _isLumiApi(value) {
  try {
    const url = new URL(typeof value === "string" ? value : value.url);
    return ["localhost", "127.0.0.1", "::1"].includes(url.hostname)
      && url.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

globalThis.fetch = async function authenticatedPopupFetch(input, init = {}) {
  if (!_isLumiApi(input)) return _nativePopupFetch(input, init);
  const token = await _popupAuthToken();
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  headers.set("X-Lumi-Client", "browser-extension-popup-v4");
  return _nativePopupFetch(input, { ...init, headers, credentials: "omit" });
};

async function _popupServer() {
  const values = await chrome.storage.local.get({ server: "http://localhost:7000" });
  return String(values.server || "http://localhost:7000").replace(/\/$/, "");
}

async function _renderPairState() {
  const badge = document.getElementById("pair-badge");
  const form = document.getElementById("pair-form");
  const disconnect = document.getElementById("pair-disconnect");
  if (!badge || !form || !disconnect) return;
  const token = await _popupAuthToken();
  if (!token) {
    badge.textContent = "Not paired";
    badge.className = "pair-badge off";
    form.hidden = false;
    disconnect.hidden = true;
    return;
  }
  try {
    const server = await _popupServer();
    const response = await fetch(`${server}/api/v4/security/me`);
    const data = await response.json();
    if (!response.ok || !data.authenticated) throw new Error(data.error || "Not paired");
    badge.textContent = `${data.client_name || "Paired"} · ${data.role}`;
    badge.className = "pair-badge on";
    form.hidden = true;
    disconnect.hidden = false;
  } catch {
    badge.textContent = "Pairing expired";
    badge.className = "pair-badge warn";
    form.hidden = false;
    disconnect.hidden = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("pair-submit");
  const input = document.getElementById("pair-code");
  const message = document.getElementById("pair-message");
  const disconnect = document.getElementById("pair-disconnect");

  button?.addEventListener("click", async () => {
    const code = String(input?.value || "").trim().toUpperCase();
    if (!code) {
      if (message) message.textContent = "Enter the code shown in Lumi settings.";
      return;
    }
    button.disabled = true;
    if (message) message.textContent = "Pairing…";
    try {
      const server = await _popupServer();
      const response = await _nativePopupFetch(`${server}/api/security/pair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          client_name: `Browser extension (${navigator.userAgent.includes("Firefox") ? "Firefox" : "Chromium"})`,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.token) throw new Error(data.error || "Pairing failed");
      await chrome.storage.local.set({ apiToken: data.token });
      if (input) input.value = "";
      if (message) message.textContent = "Paired securely.";
      chrome.runtime.sendMessage({ type: "GET_STATUS" }, () => {});
      await _renderPairState();
    } catch (error) {
      if (message) message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  input?.addEventListener("keydown", event => {
    if (event.key === "Enter") button?.click();
  });

  disconnect?.addEventListener("click", async () => {
    await chrome.storage.local.remove("apiToken");
    if (message) message.textContent = "This browser is no longer paired.";
    await _renderPairState();
  });

  void _renderPairState();
});
