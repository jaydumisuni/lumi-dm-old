"use strict";

const { app } = require("electron");
const http = require("http");

const originalRequest = http.request.bind(http);
let sessionCookie = "";
let bootstrapping = null;
let stopped = false;

function toOptions(value) {
  if (typeof value === "string" || value instanceof URL) {
    const parsed = new URL(value);
    return {
      protocol: parsed.protocol,
      hostname: parsed.hostname,
      port: parsed.port || 80,
      path: `${parsed.pathname}${parsed.search}`,
    };
  }
  return value && typeof value === "object" ? { ...value } : value;
}

function isLumiLocal(options) {
  if (!options || typeof options !== "object") return false;
  const host = String(options.hostname || options.host || "").replace(/^\[|\]$/g, "").split(":", 1)[0];
  const port = Number(options.port || 80);
  return ["127.0.0.1", "localhost", "::1"].includes(host) && port === 7000;
}

http.request = function lumiAuthenticatedRequest(input, ...rest) {
  let options = toOptions(input);
  if (isLumiLocal(options)) {
    options = { ...options, headers: { ...(options.headers || {}) } };
    const route = String(options.path || "");
    if (sessionCookie && route !== "/api/security/bootstrap") options.headers.Cookie = sessionCookie;
  }
  return originalRequest(options, ...rest);
};

http.get = function lumiAuthenticatedGet(input, ...rest) {
  const request = http.request(input, ...rest);
  request.end();
  return request;
};

function bootstrap() {
  if (sessionCookie) return Promise.resolve(sessionCookie);
  if (bootstrapping) return bootstrapping;
  bootstrapping = new Promise((resolve, reject) => {
    const request = originalRequest({
      hostname: "127.0.0.1",
      port: 7000,
      path: "/api/security/bootstrap",
      method: "GET",
      timeout: 2500,
      headers: {
        "User-Agent": "Lumi-Electron-Native/1.0",
        "X-Lumi-Client": "electron-native",
      },
    }, response => {
      response.resume();
      response.on("end", () => {
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(`Lumi native bootstrap failed (${response.statusCode})`));
          return;
        }
        const values = response.headers["set-cookie"] || [];
        const cookie = values
          .map(value => String(value).split(";", 1)[0])
          .find(value => value.startsWith("lumi_session="));
        if (!cookie) {
          reject(new Error("Lumi bootstrap returned no session"));
          return;
        }
        sessionCookie = cookie;
        resolve(cookie);
      });
    });
    request.on("timeout", () => request.destroy(new Error("bootstrap timeout")));
    request.on("error", reject);
    request.end();
  }).finally(() => { bootstrapping = null; });
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
  setInterval(() => {
    sessionCookie = "";
    keepAuthenticated();
  }, 10 * 60 * 60 * 1000);
});

app.on("before-quit", () => {
  stopped = true;
  sessionCookie = "";
});

module.exports = { bootstrap, hasSession: () => Boolean(sessionCookie) };
