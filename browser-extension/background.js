/**
 * Lumi extension production service worker.
 *
 * Security contract implemented by browser-bridge.js:
 * - isLocalServer enforcement keeps capture credentials on loopback Lumi only.
 * - Request secrets can only be sent to local Lumi.
 * - Repair capture uses /api/browser/repair-capture.
 */
import "./notification-guard.js";
import "./browser-bridge.js";
