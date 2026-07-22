/* Lumi DM extension authentication shim.
 *
 * Runs only in the extension service worker. It never injects credentials into
 * page scripts or third-party requests.
 */

const _lumiNativeFetch = globalThis.fetch.bind(globalThis);
const _lumiLoopbackHosts = new Set(["localhost", "127.0.0.1", "::1"]);

async function _lumiStoredToken() {
  const values = await chrome.storage.local.get({ apiToken: "" });
  return String(values.apiToken || "");
}

function _lumiApiRequest(value) {
  try {
    const url = new URL(typeof value === "string" ? value : value.url);
    return _lumiLoopbackHosts.has(url.hostname) && url.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

globalThis.fetch = async function lumiAuthenticatedFetch(input, init = {}) {
  if (!_lumiApiRequest(input)) return _lumiNativeFetch(input, init);
  const token = await _lumiStoredToken();
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  headers.set("X-Lumi-Client", "browser-extension-v4");
  return _lumiNativeFetch(input, {
    ...init,
    headers,
    credentials: "omit",
  });
};
