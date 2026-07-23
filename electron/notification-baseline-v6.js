"use strict";

/* Prevent Windows-login startup from replaying native notifications for every
 * download that was already complete before the current Lumi session. */
const { app, Notification } = require("electron");
const http = require("http");

const baselineNames = new Set();
let baselineReady = false;
let attempts = 0;
const originalShow = Notification.prototype.show;

function fetchBaseline() {
  attempts += 1;
  const request = http.get({ hostname: "127.0.0.1", port: 7000, path: "/api/downloads?limit=500", timeout: 4000 }, response => {
    let raw = "";
    response.setEncoding("utf8");
    response.on("data", chunk => raw += chunk);
    response.on("end", () => {
      try {
        const parsed = JSON.parse(raw || "{}");
        for (const task of parsed.downloads || []) {
          if (["completed", "failed", "cancelled", "paused", "needs_link"].includes(task.status)) {
            baselineNames.add(String(task.filename || task.metadata?.title || task.url || "File downloaded"));
          }
        }
        baselineReady = true;
      } catch (_) {}
      if (!baselineReady && attempts < 30) setTimeout(fetchBaseline, 500);
    });
  });
  request.on("timeout", () => request.destroy());
  request.on("error", () => { if (attempts < 30) setTimeout(fetchBaseline, 500); });
}

Notification.prototype.show = function patchedShow() {
  const title = String(this.title || "");
  const body = String(this.body || "");
  if (title === "Download complete") {
    if (!baselineReady && attempts < 30) return;
    if (baselineNames.has(body)) {
      baselineNames.delete(body);
      return;
    }
  }
  return originalShow.call(this);
};

app.whenReady().then(() => setTimeout(fetchBaseline, 350));

module.exports = { baselineNames };
