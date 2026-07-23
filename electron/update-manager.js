"use strict";

const { app, dialog, shell } = require("electron");
const fs = require("fs");
const path = require("path");
const https = require("https");
const crypto = require("crypto");
const { spawn } = require("child_process");

const RELEASES_API = "https://api.github.com/repos/jaydumisuni/lumi-dm/releases/latest";
const RELEASES_PAGE = "https://github.com/jaydumisuni/lumi-dm/releases";

function parseVersion(value) {
  return String(value || "0.0.0").replace(/^v/i, "").split(/[+-]/)[0].split(".").map(part => Number.parseInt(part, 10) || 0);
}

function isNewer(candidate, current) {
  const left = parseVersion(candidate);
  const right = parseVersion(current);
  for (let index = 0; index < Math.max(left.length, right.length, 3); index++) {
    const a = left[index] || 0, b = right[index] || 0;
    if (a !== b) return a > b;
  }
  return false;
}

function requestBuffer(url, headers = {}, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 8) return reject(new Error("Too many update redirects"));
    const request = https.get(url, {
      headers: { "User-Agent": "Lumi-DM-Updater/1.0", Accept: "application/vnd.github+json", ...headers },
    }, response => {
      const status = response.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && response.headers.location) {
        response.resume();
        return resolve(requestBuffer(new URL(response.headers.location, url).toString(), headers, redirects + 1));
      }
      if (status < 200 || status >= 300) {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", chunk => text += chunk);
        response.on("end", () => reject(new Error(`Update request failed (${status}): ${text.slice(0, 180)}`)));
        return;
      }
      const chunks = [];
      response.on("data", chunk => chunks.push(chunk));
      response.on("end", () => resolve(Buffer.concat(chunks)));
    });
    request.setTimeout(25000, () => request.destroy(new Error("Update request timed out")));
    request.on("error", reject);
  });
}

function downloadFile(url, destination, progress, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 8) return reject(new Error("Too many update redirects"));
    const request = https.get(url, { headers: { "User-Agent": "Lumi-DM-Updater/1.0", Accept: "application/octet-stream" } }, response => {
      const status = response.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && response.headers.location) {
        response.resume();
        return resolve(downloadFile(new URL(response.headers.location, url).toString(), destination, progress, redirects + 1));
      }
      if (status < 200 || status >= 300) {
        response.resume();
        return reject(new Error(`Update download failed (${status})`));
      }
      const total = Number(response.headers["content-length"] || 0);
      let received = 0;
      const temporary = `${destination}.part`;
      const output = fs.createWriteStream(temporary);
      response.on("data", chunk => {
        received += chunk.length;
        if (progress) progress({ received, total, percent: total ? received * 100 / total : 0 });
      });
      response.pipe(output);
      output.on("finish", () => output.close(() => {
        fs.renameSync(temporary, destination);
        resolve(destination);
      }));
      output.on("error", error => {
        try { fs.unlinkSync(temporary); } catch (_) {}
        reject(error);
      });
    });
    request.setTimeout(60000, () => request.destroy(new Error("Update download timed out")));
    request.on("error", reject);
  });
}

function platformAsset(assets) {
  const names = assets || [];
  const arch = process.arch.toLowerCase();
  const match = asset => {
    const name = String(asset.name || "").toLowerCase();
    if (process.platform === "win32") return name.endsWith(".exe") && !name.includes("blockmap") && (!name.includes("arm64") || arch === "arm64");
    if (process.platform === "darwin") return (name.endsWith(".dmg") || name.endsWith(".zip")) && (!name.includes("arm64") || arch === "arm64");
    return name.endsWith(".appimage") && (!name.includes("arm64") || arch === "arm64");
  };
  return names.find(match) || null;
}

async function expectedDigest(asset, assets) {
  if (typeof asset.digest === "string" && asset.digest.toLowerCase().startsWith("sha256:")) {
    return asset.digest.slice(7).trim().toLowerCase();
  }
  const checksum = (assets || []).find(item => {
    const name = String(item.name || "").toLowerCase();
    return name === `${String(asset.name || "").toLowerCase()}.sha256` || name === "sha256sums.txt" || name === "checksums.txt";
  });
  if (!checksum) return "";
  const text = (await requestBuffer(checksum.browser_download_url)).toString("utf8");
  const escaped = String(asset.name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const direct = text.match(new RegExp(`([a-f0-9]{64})\\s+[* ]?${escaped}`, "i"));
  const single = text.trim().match(/^([a-f0-9]{64})$/i);
  return String((direct || single || [])[1] || "").toLowerCase();
}

function sha256(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const input = fs.createReadStream(file);
    input.on("data", chunk => hash.update(chunk));
    input.on("end", () => resolve(hash.digest("hex")));
    input.on("error", reject);
  });
}

class UpdateManager {
  constructor(options = {}) {
    this.onStatus = options.onStatus || (() => {});
    this.currentVersion = app.getVersion();
    this.lastResult = null;
    this.checking = false;
    this.downloadPromise = null;
  }

  async check(manual = false) {
    if (this.checking) return this.lastResult || { available: false, currentVersion: this.currentVersion, message: "Update check already running" };
    this.checking = true;
    this.onStatus({ state: "checking" });
    try {
      const release = JSON.parse((await requestBuffer(RELEASES_API)).toString("utf8"));
      const version = String(release.tag_name || release.name || "").replace(/^v/i, "");
      const available = !release.draft && isNewer(version, this.currentVersion);
      const asset = available ? platformAsset(release.assets || []) : null;
      this.lastResult = {
        available,
        version,
        currentVersion: this.currentVersion,
        releaseUrl: release.html_url || RELEASES_PAGE,
        publishedAt: release.published_at || "",
        message: available
          ? (asset ? "A newer release is available and will be prepared securely." : "A newer release exists, but no installer matches this platform.")
          : "No newer GitHub release was found.",
      };
      this.onStatus({ state: available ? "available" : "current", ...this.lastResult });
      if (available && asset && app.isPackaged && !this.downloadPromise) {
        this.downloadPromise = this.prepare(release, asset).finally(() => { this.downloadPromise = null; });
        void this.downloadPromise;
      } else if (available && manual && !asset) {
        await shell.openExternal(this.lastResult.releaseUrl);
      }
      return this.lastResult;
    } catch (error) {
      const result = { available: false, currentVersion: this.currentVersion, error: error.message, message: error.message };
      this.lastResult = result;
      this.onStatus({ state: "error", ...result });
      if (manual) throw error;
      return result;
    } finally { this.checking = false; }
  }

  async prepare(release, asset) {
    const expected = await expectedDigest(asset, release.assets || []);
    if (!expected) {
      const result = { state: "verification-required", version: release.tag_name, releaseUrl: release.html_url || RELEASES_PAGE, message: "The release has no SHA-256 digest, so Lumi will not run it automatically." };
      this.onStatus(result);
      return result;
    }
    const directory = path.join(app.getPath("userData"), "updates");
    fs.mkdirSync(directory, { recursive: true });
    const destination = path.join(directory, path.basename(asset.name));
    this.onStatus({ state: "downloading", version: release.tag_name, percent: 0 });
    await downloadFile(asset.browser_download_url, destination, progress => this.onStatus({ state: "downloading", version: release.tag_name, ...progress }));
    const actual = await sha256(destination);
    if (actual !== expected) {
      try { fs.unlinkSync(destination); } catch (_) {}
      throw new Error("Downloaded update did not match its SHA-256 digest")
    }
    this.onStatus({ state: "ready", version: release.tag_name, path: destination, verified: true });
    const choice = await dialog.showMessageBox({
      type: "info",
      title: "Lumi update ready",
      message: `Lumi ${String(release.tag_name || "").replace(/^v/i, "")} is ready to install`,
      detail: "The installer was downloaded from GitHub Releases and its SHA-256 digest was verified.",
      buttons: ["Install now", "Later", "View release"],
      defaultId: 0,
      cancelId: 1,
    });
    if (choice.response === 2) {
      await shell.openExternal(release.html_url || RELEASES_PAGE);
      return { state: "ready", path: destination };
    }
    if (choice.response === 0) this.install(destination);
    return { state: "ready", path: destination };
  }

  install(file) {
    if (process.platform === "darwin") {
      void shell.openPath(file).then(() => app.quit());
      return;
    }
    if (process.platform !== "win32") {
      try { fs.chmodSync(file, 0o755); } catch (_) {}
    }
    const child = spawn(file, [], { detached: true, stdio: "ignore" });
    child.unref();
    setTimeout(() => app.quit(), 350);
  }
}

module.exports = { UpdateManager, isNewer, platformAsset };
