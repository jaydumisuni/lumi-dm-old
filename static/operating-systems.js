"use strict";

/* Lumi computer operating-system workspace. */
(() => {
  const osState = {
    catalogue: null,
    results: [],
    loading: false,
    family: sessionStorage.getItem("LUMI.osFamily") || "Windows",
  };

  try {
    viewMeta.operating_systems = [
      "Operating systems",
      "Official Windows, macOS and Linux installation files",
    ];
  } catch (_) {}

  window.addEventListener("DOMContentLoaded", () => {
    const view = document.getElementById("view-operating_systems");
    if (!view) return;
    view.addEventListener("click", handleClick);
    view.addEventListener("submit", handleSubmit);
    document.querySelector('[data-view="operating_systems"]')?.addEventListener("click", () => {
      setTimeout(() => void renderOsView(), 0);
    });
  });

  async function loadCatalogue() {
    if (!osState.catalogue) osState.catalogue = await osApi("GET", "/api/v5/os/catalogue");
    return osState.catalogue;
  }

  async function renderOsView() {
    const view = document.getElementById("view-operating_systems");
    if (!view) return;
    let catalogue;
    try {
      catalogue = await loadCatalogue();
    } catch (error) {
      catalogue = {
        families: ["Windows", "macOS", "Linux"],
        options: {},
        warning: error.message,
      };
    }
    view.innerHTML = `
      <div class="firmware-shell os-catalogue-shell">
        <section class="firmware-hero os-hero">
          <div class="os-hero-copy">
            <small>Technician catalogue</small>
            <h2>Computer Operating Systems</h2>
            <p>Choose Windows, macOS or Linux, then narrow the version, edition, language, architecture and channel. Official files remain first. Helpers and indexes are clearly labelled before download.</p>
          </div>
          <div class="firmware-warning"><span>⚠</span><span>${osEsc(catalogue.warning || "Verify the edition, architecture and checksum before installation.")}</span></div>
        </section>
        <div class="os-platform-grid">
          ${["Windows", "macOS", "Linux"].map(family => `
            <button class="os-platform-card ${osState.family === family ? "active" : ""}" type="button" data-os-family="${family}">
              <span class="os-platform-icon">${family === "Windows" ? "⊞" : family === "macOS" ? "◉" : "◆"}</span>
              <strong>${family}</strong>
              <small>${family === "Windows" ? "Microsoft retail ISO files" : family === "macOS" ? "Installers and restore images" : "Official distribution images"}</small>
            </button>`).join("")}
        </div>
        <div id="os-filter-host">${osFilterHtml(catalogue, osState.family)}</div>
        <div id="os-results">${osResultsHtml()}</div>
      </div>`;
  }

  function osFilterHtml(catalogue, family) {
    const options = catalogue.options?.[family] || {};
    const distributions = family === "Linux" ? options.distributions || [] : [];
    return `<form class="firmware-filters os-filters" id="os-catalogue-form">
      <input type="hidden" name="family" value="${osEsc(family)}">
      ${family === "Linux" ? `<label>Distribution<select class="select" name="distribution">${distributions.map(value => `<option value="${osEsc(value)}">${osEsc(value)}</option>`).join("")}</select></label>` : ""}
      <label>Version<select class="select" name="version"><option value="">Latest / current</option>${(options.versions || []).map(value => `<option value="${osEsc(value)}">${osEsc(value)}</option>`).join("")}</select></label>
      <label>Edition / image<select class="select" name="edition"><option value="">Recommended</option>${(options.editions || []).map(value => `<option value="${osEsc(value)}">${osEsc(value)}</option>`).join("")}</select></label>
      <label>Architecture<select class="select" name="architecture">${(options.architectures || []).map(value => `<option value="${osEsc(value)}">${osEsc(value)}</option>`).join("")}</select></label>
      <label>Channel<select class="select" name="channel">${(options.channels || ["all"]).map(value => `<option value="${osEsc(value)}">${osEsc(titleCase(value))}</option>`).join("")}</select></label>
      ${family === "Windows" ? `<label>Language<select class="select" name="language">${(options.languages || []).map(value => `<option value="${osEsc(value)}">${osEsc(value)}</option>`).join("")}</select></label>` : ""}
      <label class="os-wide">Search within results<input class="input" name="query" type="search" placeholder="version, build, edition or file name"></label>
      <div class="firmware-filter-actions"><button class="btn primary" type="submit">⌕ Find operating systems</button><button class="btn" type="button" data-os-action="clear">Clear</button></div>
    </form>`;
  }

  async function handleClick(event) {
    const familyButton = event.target.closest("[data-os-family]");
    if (familyButton) {
      osState.family = familyButton.dataset.osFamily;
      sessionStorage.setItem("LUMI.osFamily", osState.family);
      osState.results = [];
      await renderOsView();
      return;
    }
    const actionButton = event.target.closest("[data-os-action]");
    if (!actionButton) return;
    const action = actionButton.dataset.osAction;
    if (action === "clear") {
      osState.results = [];
      await renderOsView();
      return;
    }
    const item = osState.results[Number(actionButton.dataset.index)];
    if (!item) return;
    if (action === "copy") return copyOsUrl(item);
    if (action === "source") return window.open(item.source_url || item.url, "_blank", "noopener");
    if (action === "resolve") return void resolveWindows(item, actionButton);
    if (action === "download") return void stageOperatingSystem(item, actionButton);
  }

  async function handleSubmit(event) {
    if (event.target.id !== "os-catalogue-form") return;
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    osState.loading = true;
    updateResults();
    try {
      const response = await osApi("GET", `/api/v5/os/search?${new URLSearchParams(data)}`);
      osState.results = response.results || [];
    } catch (error) {
      osState.results = [];
      osToast("Operating-system search failed", error.message, "error");
    } finally {
      osState.loading = false;
      updateResults();
    }
  }

  function updateResults() {
    const host = document.getElementById("os-results");
    if (host) host.innerHTML = osResultsHtml();
  }

  function osResultsHtml() {
    if (osState.loading) return `<div class="firmware-loading">Checking official operating-system sources…</div>`;
    if (!osState.results.length) return `<div class="empty"><div class="empty-icon">◫</div><strong>Select Windows, macOS or Linux</strong>Choose a version, edition and architecture, then search.</div>`;
    const groups = {};
    for (const item of osState.results) (groups[item.source_group || "Operating systems"] ||= []).push(item);
    return `<div class="firmware-groups">${Object.entries(groups).map(([group, items]) => `
      <section class="firmware-group">
        <div class="firmware-group-head"><h3>${osEsc(group)}</h3><span>${items.length} result${items.length === 1 ? "" : "s"}</span></div>
        <div class="firmware-list">${items.map(osCard).join("")}</div>
      </section>`).join("")}</div>`;
  }

  function osCard(item) {
    const index = osState.results.indexOf(item);
    const resolver = item.metadata?.resolver === "fido" && !item.direct;
    const host = safeHost(item.url || item.source_url);
    return `<article class="firmware-card ${item.official ? "official" : ""} ${resolver ? "os-resolver" : ""}">
      <div class="firmware-card-head"><div class="firmware-source-icon">${item.official ? "✓" : resolver ? "W" : "⌁"}</div><div class="firmware-title"><h4>${osEsc(item.title || item.filename || item.source_name)}</h4><p>${osEsc(item.source_name)} · ${osEsc(item.device || item.brand)}</p></div></div>
      <div class="firmware-badges"><span class="firmware-badge ${item.official ? "good" : "warn"}">${item.official ? "Official source" : resolver ? "Official-file helper" : "Source index"}</span>${item.channel ? `<span class="firmware-badge">${osEsc(item.channel)}</span>` : ""}${item.file_type ? `<span class="firmware-badge">${osEsc(item.file_type)}</span>` : ""}</div>
      <div class="firmware-details"><div class="firmware-detail"><span>Version</span><strong>${osEsc(item.version || "—")}</strong></div><div class="firmware-detail"><span>Architecture</span><strong>${osEsc(item.metadata?.architecture || item.device || "—")}</strong></div><div class="firmware-detail"><span>Size / host</span><strong>${item.size ? osFmtBytes(item.size) : osEsc(host || "—")}</strong></div></div>
      ${item.sha256 ? `<div class="firmware-detail os-checksum-row"><span>SHA-256</span><strong class="os-checksum good" title="${osEsc(item.sha256)}">${osEsc(item.sha256)}</strong></div>` : ""}
      <div class="firmware-notes">${osEsc(item.notes || "Confirm compatibility and verify the source before installation.")}</div>
      ${resolver ? `<div class="os-licence">Fido is an external GPLv3 helper by Pete Batard. Lumi requests a temporary Microsoft-hosted retail ISO URL only after you click Resolve.</div>` : ""}
      <div class="firmware-actions">${resolver ? `<button class="btn primary" type="button" data-os-action="resolve" data-index="${index}">Resolve official link</button>` : item.direct && item.url ? `<button class="btn primary" type="button" data-os-action="download" data-index="${index}">↓ Download in Lumi</button>` : ""}${item.url ? `<button class="btn" type="button" data-os-action="copy" data-index="${index}">Copy URL</button>` : ""}${item.source_url || item.url ? `<button class="btn" type="button" data-os-action="source" data-index="${index}">Open source</button>` : ""}</div>
    </article>`;
  }

  async function resolveWindows(item, button) {
    const form = document.getElementById("os-catalogue-form");
    const data = Object.fromEntries(new FormData(form).entries());
    button.disabled = true;
    button.textContent = "Resolving with Fido…";
    try {
      const response = await osApi("POST", "/api/v5/os/windows/resolve", {
        version: data.version || item.version || "Windows 11",
        edition: data.edition || item.file_type || "Home/Pro",
        language: data.language || item.metadata?.language || "English International",
        architecture: data.architecture || item.metadata?.architecture || "x64",
      });
      osState.results.unshift(response.result);
      updateResults();
      osToast("Official Microsoft link resolved", "The temporary Microsoft ISO URL is ready. Confirm it before starting the download.", "success");
    } catch (error) {
      osToast("Windows link not resolved", error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Resolve official link";
    }
  }

  async function stageOperatingSystem(item, button) {
    const family = item.metadata?.os_family || item.brand || osState.family;
    const details = [family, item.version, item.file_type, item.metadata?.architecture || item.device].filter(Boolean).join(" · ");
    if (!window.confirm(`Download this operating-system image with Lumi?\n\n${item.title}\n${details}\nSource: ${item.source_name}\n\nVerify the checksum and target architecture before installation.`)) return;
    const defaultDir = (typeof state !== "undefined" && state.settings?.default_dir) || "";
    const targetDir = window.prompt("Save operating-system image to:", defaultDir || "") ?? null;
    if (targetDir === null) return;
    button.disabled = true;
    try {
      const task = await osApi("POST", "/api/v5/os/stage", {
        url: item.url,
        filename: item.filename || "",
        target_dir: targetDir,
        family,
        distribution: item.metadata?.distribution || "",
        version: item.version,
        edition: item.file_type,
        architecture: item.metadata?.architecture || item.device,
        channel: item.channel,
        provider: item.provider,
        source_name: item.source_name,
        source_url: item.source_url,
        sha256: item.sha256,
      });
      await osApi("POST", `/api/downloads/${encodeURIComponent(task.id)}/confirm`, {
        filename: task.filename,
        target_dir: targetDir,
        connections: 0,
      });
      if (typeof refreshFoundation === "function") await refreshFoundation();
      osToast("Operating system queued", item.filename || item.title, "success");
      if (typeof switchView === "function") switchView("downloads");
    } catch (error) {
      osToast("Operating system not queued", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function copyOsUrl(item) {
    try {
      await navigator.clipboard.writeText(item.url || item.source_url);
      osToast("URL copied", item.filename || item.source_name, "success");
    } catch {
      osToast("Could not copy", "Open the source and copy the link manually.", "error");
    }
  }

  async function osApi(method, path, body = null) {
    if (typeof v5Api === "function") return v5Api(method, path, body);
    const response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `${method} ${path} failed`);
    return data;
  }

  function osToast(title, message, type) {
    if (typeof v5Toast === "function") v5Toast(title, message, type);
    else window.alert(`${title}\n${message}`);
  }

  function safeHost(url) { try { return new URL(url).hostname; } catch { return ""; } }
  function titleCase(value) { return String(value || "").replace(/_/g, " ").replace(/\b\w/g, character => character.toUpperCase()); }
  function osFmtBytes(value) { const size = Number(value || 0); if (size >= 1073741824) return `${(size / 1073741824).toFixed(2)} GB`; if (size >= 1048576) return `${(size / 1048576).toFixed(1)} MB`; return `${Math.round(size / 1024)} KB`; }
  function osEsc(value) { return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])); }
})();
