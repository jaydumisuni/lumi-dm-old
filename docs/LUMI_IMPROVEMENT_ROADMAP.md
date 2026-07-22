# Lumi DM Improvement Roadmap

This roadmap compares Lumi DM with AB Download Manager, Gopeed, JDownloader-style workflows, and the supplied Free Download Manager references. The goal is not to copy another product or overload Lumi. The goal is to strengthen Lumi's own engine, reliability, browser hand-off, and everyday workflow.

## Source and licence boundary

Lumi currently has no repository `LICENSE` file. Choose Lumi's licence before importing third-party source code.

- **AB Download Manager** uses Apache-2.0. Small reusable portions are possible only with the required licence and attribution notices, but its Kotlin architecture is different from Lumi's Python/Electron stack. Prefer clean Lumi implementations of the useful behaviours.
- **Gopeed** uses GPL-3.0. Do not copy Gopeed source into Lumi unless Lumi is intentionally released under GPL-3.0. Use Gopeed only as a behavioural and architectural reference.
- **gopeed-extension-youtube** declares ISC in `package.json`. Its ideas are permissive to study, but Lumi already has `yt-dlp` and FFmpeg integration, so direct source borrowing is unnecessary.
- The supplied **DreamPack FreeDownloadManager** and **johna23-lab/jdownloader2** repositories are extremely small presentation/packaging repositories, not useful full download-engine sources.

## P0 — Correctness and recovery

These improvements should land before expanding the feature list.

### 1. Safe segmented downloads

The Python parallel path must validate every segment response:

- Require HTTP `206 Partial Content` for ranged segment requests.
- Validate `Content-Range` start/end and total size.
- If a server ignores ranges or changes the object, stop all segment workers and fall back safely to one connection.
- Never allow multiple workers to write full `200 OK` responses into different offsets of the same file.

### 2. True segmented resume

Persist a sidecar record for each partial download containing:

- Original and final URL
- File size
- ETag and Last-Modified validators
- Segment boundaries and completed byte offsets
- Connection count and retry state

On resume, verify that the remote object is still the same before continuing. If validators changed, ask whether to restart rather than silently mixing old and new bytes.

### 3. Real queue controller

Replace the replaceable semaphore and 30-second acquire timeout with a central queue manager:

- Pending downloads wait instead of failing because active slots are busy.
- Changing the concurrency limit does not corrupt slot accounting.
- Support priority, move up/down, start paused, and fair scheduling.
- Keep HTTP, torrent, and video tasks under the same queue policy.

### 4. Atomic state and recovery journal

- Save job state immediately after important transitions.
- Use schema versioning and recover from a damaged JSON file.
- Keep a backup of the last valid state.
- Record restart reason and interrupted tasks.

### 5. LAN/API security

Lumi can bind to `0.0.0.0` for phone access. Add:

- Generated API token
- Authentication on all write endpoints
- Optional read-only LAN mode
- Restricted CORS/origin policy
- A pairing screen or QR code rather than exposing an unauthenticated local API

## P1 — High-value daily features

### 6. Queues and scheduler

Add named queues with:

- Start/end time and selected days
- Per-queue maximum concurrent downloads
- Queue speed cap
- Start, pause, or shut down when a queue completes
- Manual reorder and priority

### 7. Per-host request profiles

Store optional settings by host:

- User-Agent
- Referer
- Cookies/browser session hand-off
- Custom headers
- Username/password
- Proxy
- Certificate verification policy
- Connection and retry limits

Secrets must be stored using the operating system credential store, not plaintext JSON.

### 8. Retry and expired-link recovery

- Configurable retry count with exponential backoff and jitter
- Retry after temporary network loss
- Distinguish DNS, timeout, certificate, authentication, storage, and HTTP errors
- Allow a failed or paused task to receive a replacement URL without losing downloaded bytes
- Let the browser extension refresh signed/temporary URLs

### 9. Import, export, and diagnostics

- Export/import queue and history as versioned JSON
- Export an individual request as a safe `curl` command with secrets redacted by default
- Structured rotating logs
- One-click diagnostics bundle containing logs, settings summary, capabilities, and failed-task metadata

### 10. Duplicate handling and file organisation

- Detect the same URL, final URL, filename, and optional checksum
- Offer: focus existing task, update URL, overwrite, auto-rename, or create another copy
- Categories and folder rules by extension, MIME type, host, and date
- Filename templates such as `{category}/{year}-{month}/{filename}`

### 11. Browser integration hardening

- Keep interception optional and easy to pause per site
- Forward Referer and selected browser cookies only with explicit user approval
- Add a minimum file-size threshold and excluded-domain list
- Show why a browser download was intercepted
- Use least-privilege extension permissions where possible

### 12. Keep-awake and completion automation

- Prevent sleep while active downloads need the network
- Restore normal power behaviour immediately when idle
- Keep existing sleep/shutdown/restart actions
- Add optional webhook and local script actions with clear security warnings

## P2 — Capability expansion

### 13. Archive extraction

Optional 7-Zip-backed extraction:

- ZIP, 7z, RAR, TAR and split archives
- Password support
- Wait for every archive part
- Delete archive after successful extraction only
- Preserve executable permissions on macOS/Linux

### 14. Video workflow improvements

Build on Lumi's existing `yt-dlp` backend:

- Load actual formats for the pasted URL instead of relying only on fixed presets
- Show video codec, audio codec, resolution, frame rate and estimated size
- Automatically merge separate audio/video with bundled FFmpeg
- Playlist item selection and range support
- Optional browser-cookie import for sites requiring login
- Clear site/legal notice and no DRM circumvention

### 15. Provider/extension interface

Start with a small internal provider interface rather than a public store:

- URL matcher
- Metadata/format resolver
- Request transformer
- Optional post-processing step
- Capability and permission declaration

Providers run with restricted permissions and cannot access arbitrary local files by default.

### 16. Torrent improvements

- Select files before starting
- File priority
- Tracker management
- Seed ratio/time limits
- Magnet metadata progress
- Clear separation between download and seeding status

### 17. Checksums and integrity

Lumi already supports manual checksum verification. Add:

- Auto-detect SHA256/SHA1/MD5 sidecar links where provided
- Content-MD5 support
- Hash calculation progress
- Save verified status in history
- Re-verify from the task menu

## P3 — Product polish

- Search, sort, filters and saved views
- Configurable columns
- Light/dark/system themes after engine work is stable
- Keyboard shortcuts and accessibility pass
- Translations after UI strings are centralised
- Portable Windows build and arm64 targets
- Signed update feed generated by THETECHGUY Software Builder
- Release notes and rollback support

## Recommended implementation order

1. Range-response validation and segmented-resume sidecar
2. Queue controller and scheduler foundation
3. LAN API token and secure browser hand-off
4. Per-host request profiles and URL replacement
5. Logs, diagnostics, import/export and duplicate handling
6. Archive extraction and improved video format workflow
7. Provider interface, categories, torrent controls and polish

## Builder integration

THETECHGUY Software Builder should:

- Pull only Lumi source and approved build metadata
- Run the branding guard and tests
- Build the Python server and Electron wrapper offline
- Generate icons and platform packages
- Sign releases
- Store build caches outside the Lumi repository
- Publish only approved installers and release metadata

Generated folders such as `node_modules`, virtual environments, PyInstaller work output, unpacked Electron apps, installer output, caches, and signing material must remain outside source control.
