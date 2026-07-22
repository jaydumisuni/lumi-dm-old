/**
 * Lumi extension API authentication bootstrap.
 *
 * This module wraps fetch before the main background worker loads. Only requests
 * to the configured loopback Lumi `/api/` namespace receive an Authorization
 * header. The raw token remains in extension-local storage.
 */

const nativeFetch = globalThis.fetch.bind(globalThis);
const DEFAULT_SERVER = "http://localhost:7000";

let configuredServer = DEFAULT_SERVER;
let authToken = "";
let bootstrapPromise = null;

const initial = await chrome.storage.local.get({
  server: DEFAULT_SERVER,
  lumiAuthToken: "",
});
configuredServer = normaliseServer(initial.server);
authToken = String(initial.lumiAuthToken || "");

chrome.storage.onChanged.addListener(changes => {
  if (changes.server) {
    configuredServer = normaliseServer(changes.server.newValue);
    authToken = "";
    bootstrapPromise = null;
    chrome.storage.local.remove("lumiAuthToken");
  }
  if (changes.lumiAuthToken) {
    authToken = String(changes.lumiAuthToken.newValue || "");
  }
});

function normaliseServer(value) {
  return String(value || DEFAULT_SERVER).replace(/\/$/, "");
}

function isLoopbackApi(input) {
  try {
    const url = new URL(typeof input === "string" ? input : input.url);
    const server = new URL(configuredServer);
    const loopback = ["localhost", "127.0.0.1", "::1"].includes(
      server.hostname.toLowerCase(),
    );
    return (
      loopback
      && url.origin === server.origin
      && url.pathname.startsWith("/api/")
      && !["/api/auth/bootstrap", "/api/auth/pair"].includes(url.pathname)
    );
  } catch {
    return false;
  }
}

async function ensureAuthToken() {
  if (authToken) return authToken;
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    const response = await nativeFetch(`${configuredServer}/api/auth/bootstrap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Lumi browser extension",
        kind: "extension",
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.token) {
      throw new Error(data.error || `Lumi authentication failed (${response.status})`);
    }
    authToken = String(data.token);
    await chrome.storage.local.set({ lumiAuthToken: authToken });
    return authToken;
  })().finally(() => {
    bootstrapPromise = null;
  });
  return bootstrapPromise;
}

async function authenticatedFetch(input, init = {}, retried = false) {
  if (!isLoopbackApi(input)) return nativeFetch(input, init);
  const token = await ensureAuthToken();
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  headers.set("Authorization", `Bearer ${token}`);
  const response = await nativeFetch(input, { ...init, headers });
  if (response.status === 401 && !retried) {
    authToken = "";
    await chrome.storage.local.remove("lumiAuthToken");
    return authenticatedFetch(input, init, true);
  }
  return response;
}

globalThis.fetch = authenticatedFetch;
