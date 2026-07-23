# THETECHGUY App Shell Standard v1

This is the locked desktop-window standard for THETECHGUY DIGITAL SOLUTIONS applications.

## Visual baseline

The application owns the full window surface. Windows, macOS or Linux must not add the normal platform title bar above the product UI.

The title bar is a compact dark strip with a thin purple-blue border and contains:

- Left: product icon, product name and optional suite label.
- Right: notification bell, settings gear, divider, minimize, maximize/restore and close.
- Empty title-bar space is draggable. Controls, menus and inputs are always marked as non-draggable.
- Close uses a red hover state. All other controls use the product purple-blue hover state.

## Bell behaviour

The bell opens a small pending-items list rather than a full page. Each item routes directly to the relevant task, customer, payment, update or warning. The badge shows unread count only.

## Gear behaviour

There is only one Settings entry in the application shell. The gear menu order is:

1. Settings
2. Check for updates
3. Help
4. About
5. Advanced diagnostics

Diagnostics is not a normal everyday settings page. It is an advanced recovery and evidence surface for health checks, logs, database backup/repair, missing-file detection and support exports.

## Branding

Every logo uses `object-fit: contain`, retains its source aspect ratio and is rendered from a surface-appropriate high-DPI asset. Stretching or cropping the product mark is a build failure.

## Builder requirement

The THETECHGUY Software Builder must consume `assets/ttg-app-shell-standard.json` and generate the same shell for every desktop application. Generated applications must be tested at 100%, 125%, 150% and 200% Windows display scaling.
