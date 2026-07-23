"use strict";

/* THETECHGUY App Shell Standard v2 refinements. */
(() => {
  if (!window.electronApp?.isElectron) return;

  const THEME_KEY = "TTG.shell.theme";
  const allowedThemes = new Set(["system", "dark", "light"]);
  const systemTheme = window.matchMedia("(prefers-color-scheme: light)");

  window.addEventListener("DOMContentLoaded", () => {
    applyTheme(readTheme());
    enhanceGearMenu();
    reinforceShellNavigation();
    systemTheme.addEventListener?.("change", () => {
      if (readTheme() === "system") applyTheme("system");
    });
  }, { once: true });

  function readTheme() {
    const value = localStorage.getItem(THEME_KEY) || "system";
    return allowedThemes.has(value) ? value : "system";
  }

  function resolvedTheme(value) {
    return value === "system" ? (systemTheme.matches ? "light" : "dark") : value;
  }

  function applyTheme(value) {
    const selected = allowedThemes.has(value) ? value : "system";
    const resolved = resolvedTheme(selected);
    document.documentElement.dataset.ttgTheme = resolved;
    document.documentElement.dataset.ttgThemeChoice = selected;
    document.querySelectorAll("[data-ttg-theme]").forEach(button => {
      button.classList.toggle("active", button.dataset.ttgTheme === selected);
      button.setAttribute("aria-pressed", String(button.dataset.ttgTheme === selected));
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = resolved === "light" ? "#eef2f8" : "#0b0e13";
  }

  function setTheme(value) {
    if (!allowedThemes.has(value)) return;
    localStorage.setItem(THEME_KEY, value);
    applyTheme(value);
  }

  function enhanceGearMenu() {
    const menu = document.getElementById("ttg-gear-menu");
    const head = menu?.querySelector(".ttg-shell-menu-head");
    if (!menu || !head || menu.querySelector(".ttg-theme-control")) return;
    head.insertAdjacentHTML("afterend", `
      <div class="ttg-theme-control" role="group" aria-label="Appearance">
        <span>Appearance</span>
        <div class="ttg-theme-segment">
          <button type="button" data-ttg-theme="system" aria-pressed="false">System</button>
          <button type="button" data-ttg-theme="dark" aria-pressed="false">Dark</button>
          <button type="button" data-ttg-theme="light" aria-pressed="false">Light</button>
        </div>
      </div>
      <hr class="ttg-theme-divider">`);
    menu.addEventListener("click", event => {
      const button = event.target.closest("[data-ttg-theme]");
      if (!button) return;
      event.stopPropagation();
      setTheme(button.dataset.ttgTheme);
    });
    applyTheme(readTheme());
  }

  function reinforceShellNavigation() {
    // Settings and diagnostics are intentional hidden workspaces opened only from
    // the title-bar gear. They must never return to the everyday sidebar.
    document.querySelectorAll('[data-view="settings"],[data-view="diagnostics"]').forEach(button => button.remove());
  }
})();
