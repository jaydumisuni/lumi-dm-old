# THETECHGUY App Shell Standard v2

This is the locked desktop-window standard for THETECHGUY DIGITAL SOLUTIONS applications.

## Visual baseline

The application owns the full window surface. Windows, macOS or Linux must not add the normal platform title bar above the product UI.

The title bar is a compact 43 px dark strip with a thin purple-blue outer border and contains:

- Left: product icon, product name and optional suite label.
- Right: notification bell, settings gear, divider, minimize, maximize/restore and close.
- Empty title-bar space is draggable. Controls, menus and inputs are always non-draggable.
- Close uses a red hover state. All other controls use the product purple-blue hover state.
- Maximized windows remove the outer corner radius while normal windows use 12 px corners.

## Bell behaviour

The bell opens a small pending-items list rather than a full page. Each item routes directly to the relevant task, customer, payment, update or warning. The badge shows unread count only.

The bell is not repeated in the sidebar.

## Gear behaviour

There is only one Settings entry in the application shell. The gear contains an appearance selector followed by the product controls.

Appearance:

1. System
2. Dark
3. Light

Menu order:

1. Settings
2. Check for updates
3. Help
4. About
5. Advanced diagnostics

Settings and Diagnostics must not appear as everyday sidebar destinations.

## Advanced diagnostics

Diagnostics is not a normal everyday settings page. It is the advanced recovery and evidence surface for:

- health checks;
- sanitized logs;
- database backup and repair;
- missing-file detection;
- extension and authentication status;
- privacy-safe support exports.

The only normal entry is **Gear → Advanced diagnostics**.

## Technician navigation

Mobile firmware and computer operating systems are separate sidebar workspaces.

- **Mobile firmware** contains phone/tablet firmware and ROM sources.
- **Operating systems** contains Windows, macOS and Linux selectors with source, architecture and checksum evidence.

## Branding

Every logo uses `object-fit: contain`, retains its source aspect ratio and is rendered from a surface-appropriate high-DPI asset. Stretching or cropping the product mark is a build failure.

## Builder requirement

The THETECHGUY Software Builder must consume `assets/ttg-app-shell-standard.json` and generate the same shell for every desktop application. Generated applications must be tested at 100%, 125%, 150% and 200% Windows display scaling in normal, maximized, inactive, bell-open, gear-open, dark-theme and light-theme states.
