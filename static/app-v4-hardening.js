"use strict";

// Independent final-UI guards. Loaded after app-v4.js so the product remains
// readable while these narrow lifecycle rules are reviewed separately.
state.inspectorId = null;

window.openInspector = async function openInspectorGuarded(taskId) {
  state.inspectorId = taskId;
  state.inspectorTab = "overview";
  document.getElementById("drawer-backdrop").hidden = false;
  document.getElementById("inspector").hidden = false;
  document.getElementById("inspector-body").innerHTML =
    `<div class="empty">Loading task details…</div>`;
  await window.refreshInspector(true);
};

window.refreshInspector = async function refreshInspectorGuarded(render = true) {
  const taskId = state.inspectorId || state.inspector?.task?.id;
  if (!taskId) return;
  try {
    state.inspector = await api(
      "GET",
      `/api/v4/tasks/${encodeURIComponent(taskId)}/inspector`,
    );
    state.inspectorId = state.inspector.task.id;
    if (render) renderInspector();
  } catch (error) {
    const body = document.getElementById("inspector-body");
    if (body) body.innerHTML = emptyState("Inspector unavailable", error.message);
  }
};

window.closeInspector = function closeInspectorGuarded() {
  document.getElementById("inspector").hidden = true;
  document.getElementById("drawer-backdrop").hidden = true;
  state.inspector = null;
  state.inspectorId = null;
};

const _readOnlySafeActions = new Set([
  "view-all", "status-filter", "refresh", "settings-tab",
  "refresh-diagnostics",
]);
const _readOnlySafeForms = new Set([
  "grabber", "media-inspect", "torrent-inspect",
]);

function _canWrite() {
  return (state.auth?.role || "owner") === "owner";
}

function _applyReadOnlyPresentation() {
  const writable = _canWrite();
  document.body.classList.toggle("read-only-client", !writable);
  const add = document.getElementById("new-download-btn");
  if (add) {
    add.disabled = !writable;
    add.title = writable ? "New download" : "This paired client is read-only";
  }
  document.querySelectorAll("[data-write-only]").forEach(element => {
    element.hidden = !writable;
  });
}

document.addEventListener("click", event => {
  if (_canWrite()) return;
  const button = event.target.closest("[data-action]");
  if (button && !_readOnlySafeActions.has(button.dataset.action)) {
    event.preventDefault();
    event.stopImmediatePropagation();
    toast(
      "Read-only client",
      "This paired client can inspect Lumi but cannot change tasks or settings.",
      "warning",
    );
  }
  if (event.target.closest("#new-download-btn")) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);

document.addEventListener("submit", event => {
  if (_canWrite()) return;
  const form = event.target;
  const formType = form.dataset.form || form.dataset.sourceForm || "";
  if (_readOnlySafeForms.has(formType)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  toast(
    "Read-only client",
    "Pair this device with the Owner role to make changes.",
    "warning",
  );
}, true);

document.addEventListener("DOMContentLoaded", () => {
  _applyReadOnlyPresentation();
  setInterval(_applyReadOnlyPresentation, 1500);
});
