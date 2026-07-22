/* Lumi DM extension authentication shim.
 *
 * Runs only in the extension service worker. It never injects credentials into
 * page scripts or third-party requests. A token is attached only when the
 * request origin exactly matches the server configured in extension storage.
 */

const _lumiNativeFetch = globalThis.fetch.bind(globalThis);

async function _lumiSecuritySettings() {
  const values = await chrome.storage.local.get({
    apiToken: "",
    server: "http://localhost:7000",
  });
  let serverOrigin = "";
  try { serverOrigin = new URL(values.server).origin; } catch {}
  return {
    token: String(values.apiToken || ""),
    serverOrigin,
  };
}

function _lumiRequestUrl(value) {
  try { return new URL(typeof value === "string" ? value : value.url); }
  catch { return null; }
}

globalThis.fetch = async function lumiAuthenticatedFetch(input, init = {}) {
  const url = _lumiRequestUrl(input);
  if (!url || !url.pathname.startsWith("/api/")) {
    return _lumiNativeFetch(input, init);
  }
  const settings = await _lumiSecuritySettings();
  if (!settings.serverOrigin || url.origin !== settings.serverOrigin) {
    return _lumiNativeFetch(input, init);
  }
  const headers = new Headers(
    init.headers || (input instanceof Request ? input.headers : undefined)
  );
  if (settings.token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${settings.token}`);
  }
  headers.set("X-Lumi-Client", "browser-extension-v4");
  return _lumiNativeFetch(input, {
    ...init,
    headers,
    credentials: "omit",
  });
};
