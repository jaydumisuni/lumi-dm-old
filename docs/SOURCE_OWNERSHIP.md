# Lumi DM Source Ownership Boundary

Lumi owns application behavior. A separate external Builder owns packaging.

## Lumi repository owns

- download, queue, resolver and post-processing source;
- Flask APIs and persistent state contracts;
- browser-extension source;
- Electron desktop-shell source;
- product UI, assets and one high-resolution brand source;
- runtime dependency declarations;
- tests, diagnostics contracts and developer documentation.

## External Builder owns

- Electron and Python packaging environments;
- installer engines and templates;
- icon conversion to ICO/ICNS and platform-specific sizes;
- dependency caches and temporary workspaces;
- FFmpeg, 7-Zip, aria2 and other bundled runtime binaries;
- signing certificates, notarization and release manifests;
- final EXE, DMG, AppImage, APK, IPA and installer artifacts;
- update sidecars and distribution-channel promotion.

## Non-negotiable rule

The Builder receives a source tree whose application behavior is already working
and proven. It may package, sign and distribute Lumi, but it must not repair,
redesign, simulate or complete Lumi functions during a build.

Generated outputs, toolchain caches, installer scripts and signing material do not
belong in this repository.
