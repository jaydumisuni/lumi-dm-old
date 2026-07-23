"use strict";

const assert = require("assert");
const path = require("path");

const created = [];
const cleared = [];
const badge = [];
let storageState = {};

function notificationCreate(idOrOptions, optionsOrCallback, maybeCallback) {
  const id = typeof idOrOptions === "string" ? idOrOptions : `generated-${created.length + 1}`;
  const options = typeof idOrOptions === "string" ? optionsOrCallback : idOrOptions;
  const callback = typeof idOrOptions === "string" ? maybeCallback : optionsOrCallback;
  created.push({ id, options });
  if (typeof callback === "function") callback(id);
}

notificationCreate.bind = Function.prototype.bind;

global.chrome = {
  runtime: { lastError: null },
  notifications: {
    create: notificationCreate,
    clear(id, callback) { cleared.push(id); if (callback) callback(true); },
    getAll(callback) { callback({ "legacy-1": true, "legacy-2": true }); },
  },
  storage: {
    local: {
      async get(defaults) { return { ...defaults, ...storageState }; },
      async set(value) { storageState = { ...storageState, ...value }; },
    },
  },
  action: {
    async setBadgeBackgroundColor(value) { badge.push(["background", value]); },
    async setBadgeText(value) { badge.push(["text", value]); },
    async setTitle(value) { badge.push(["title", value]); },
  },
};

require(path.resolve(__dirname, "..", "browser-extension", "notification-guard.js"));

async function main() {
  assert(cleared.includes("legacy-1") && cleared.includes("legacy-2"), "old extension notifications must be cleared when the fixed worker loads");

  const first = chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title: "Lumi DM — Not reachable",
    message: "Cannot reach http://localhost:7000 — is Lumi running?",
  });
  const second = chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title: "Lumi DM — Not reachable",
    message: "Cannot reach http://localhost:7000 — is Lumi running?",
  });
  await Promise.all([first, second]);
  assert.strictEqual(created.length, 0, "automatic unreachable events must not create Windows notifications");
  assert(badge.some(([kind, value]) => kind === "text" && value.text === "!"), "offline state must remain visible on the extension badge");

  const manualOne = chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title: "Lumi DM",
    message: "Failed to fetch",
  });
  const manualTwo = chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title: "Lumi DM",
    message: "Failed to fetch",
  });
  await Promise.all([manualOne, manualTwo]);
  assert.strictEqual(created.length, 1, "manual connectivity errors may show only one cooldown-controlled warning");
  assert.strictEqual(created[0].id, "LUMIDM-connectivity-state", "connectivity warning must replace one stable notification ID");

  await chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon48.png",
    title: "Lumi DM — Choose download options",
    message: "firmware.zip",
  });
  assert(cleared.includes("LUMIDM-connectivity-state"), "a successful handoff must clear the connectivity warning");
  assert(badge.some(([kind, value]) => kind === "text" && value.text === ""), "a successful handoff must clear the offline badge");

  console.log("notification guard proof passed");
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
