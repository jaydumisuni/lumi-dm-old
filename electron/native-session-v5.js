"use strict";

/**
 * Authenticates Lumi's native Electron processes against the protected local API.
 *
 * The Flask security boundary already exposes a loopback-only bootstrap that
 * returns an HttpOnly owner session. Electron's Node-side widget/setup requests do
 * not share Chromium's cookie jar, so this module captures that same session cookie
 * and injects it only into requests aimed at 127.0.0.1:7000.
 */
const { app } = require("electron");
const http = require("http");

const originalRequest = http.request.bind(http);
let sessionCookie = "";
let bootstrapping = null;
let stopped = false;

function isLumiLocal(options) {
  if (!options || typeof options !== "object") return false;
  const host = String(options.hostname || options.host || "").split(":", 1)[0];
  const port = Number(options.port || 80);
  return ["127.0.0.1", "localhost", "::1"].includes(host) && port === 7000;
}

http.request = function lumiAuthenticatedRequest(options, ...rest) {
  if (isLumiLocal(options)) {
    options = { ...options, headers: { ...(options.headers || {}) } };
    const route = String(options.path || "");
    if (sessionCookie && route !== "/api/security/bootstrap") {
      options.headers.Cookie = sessionCookie;
    }
  }
  return originalRequest(options, ...rest);
};

function bootstrap() {
  if (sessionCookie) return Promise.resolve(sessionCookie);
  if (bootstrapping) return bootstrapping;
  bootstrapping = new Promise((resolve, reject) => {
    const request = originalRequest(
      {
        hostname: "127.0.0.1",
        port: 7000,
        path: "/api/security/bootstrap",
        method: "GET",
        timeout: 2500,
        headers: {
          "User-Agent": "Lumi-Electron-Native/5.0",
          "X-Lumi-Client": "electron-native-v5",
        },
      },
      response => {
        response.resume();
        response.on("end", () => {
          if ((response.statusCode || 500) >= 400) {
            return reject(
              new Error(`Lumi native bootstrap failed (${response.statusCode})`)
            );
          }
          const values = response.headers["set-cookie"] || [];
          const cookie = values
            .map(value => String(value).split(";", 1)[0])
            .find(value => value.startsWith("lumi_session="));
          if (!cookie) return reject(new Error("Lumi bootstrap returned no session"));
          sessionCookie = cookie;
          resolve(cookie);
        });
      }
    );
    request.on("timeout", () => request.destroy(new Error("bootstrap timeout")));
    request.on("error", reject);
    request.end();
  }).finally(() => {
    bootstrapping = null;
  });
  return bootstrapping;
}

function keepAuthenticated() {
  if (stopped || sessionCookie) return;
  void bootstrap().catch(() => {
    if (!stopped) setTimeout(keepAuthenticated, 500);
  });
}

app.whenReady().then(() => {
  keepAuthenticated();
  // Refresh well before the server's 12-hour native session expires.
  setInterval(() => {
    sessionCookie = "";
    keepAuthenticated();
  }, 10 * 60 * 60 * 1000);
});

app.on("before-quit", () => {
  stopped = true;
  sessionCookie = "";
});

module.exports = {
  bootstrap,
  hasSession: () => Boolean(sessionCookie),
};
