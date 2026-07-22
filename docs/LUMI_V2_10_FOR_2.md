# Lumi DM V2 — 10-for-2 Execution Ledger

Lumi is completed and proved as a running application before any external Builder packages it.
Packaging is not allowed to repair unfinished application behaviour.

## 10-for-2 method

Each implementation wave contains **ten connected engineering fronts**. The wave is then closed by **two independent review gates**:

1. **Sergeant primary review** — evidence-grounded correctness, architecture, tests, security, performance and concurrency review.
2. **Independent witness review** — maintainer review plus CodeRabbit PR review. Every accepted finding becomes a permanent regression test or contract before Sergeant reruns.

A wave does not pass because code exists or compiles. It passes only when its runtime proofs succeed and both review gates are closed.

## Permanent proof law

```text
Understand
→ Build
→ Run
→ Challenge
→ Fix
→ Add regression
→ Sergeant rerun
→ Independent rerun
→ Freeze evidence
→ Advance
```

## Wave 1 — Foundation and reliable HTTP

1. Unified task model for HTTP, FTP, torrent, video and future providers.
2. Replayable request envelope with secret-safe public views.
3. SQLite task, queue, event and settings storage.
4. Atomic segment-resume journals.
5. Restart recovery for interrupted tasks.
6. Persistent named queue controller with priorities and limits.
7. Strict range and remote-identity validation.
8. Adaptive segmented HTTP transfer with dynamic largest-range splitting.
9. Repair Download Link task patching.
10. Flask runtime integration and permanent proof tests.

### Wave 1 proof targets

- Exact byte-for-byte parallel HTTP output.
- Rejection of servers that advertise ranges but ignore segment requests.
- Pause, process restart and continuation from persisted segment offsets.
- High-priority queue ordering.
- Active-task recovery to a safe paused state.
- Secret redaction in public request evidence.

## Wave 2 — Organisation, browser capture and source resolution

1. Categories and automatic placement rules.
2. Separate temporary and final storage policies.
3. Duplicate task and duplicate file strategies.
4. Per-host connection, credential, proxy and speed profiles.
5. Browser request-envelope capture.
6. Force, bypass and per-host interception rules.
7. Repair-link capture mode in the extension.
8. Unified resource resolver contract.
9. Improved page LinkGrabber with probe evidence.
10. Secure local credential/session vault.

## Wave 3 — Media, torrents and post-processing

1. Full yt-dlp format and playlist selection.
2. Subtitle, thumbnail and metadata handling.
3. FFmpeg merge and conversion jobs with progress.
4. Torrent metadata and selectable files.
5. Torrent priorities, peers, ratio and seeding controls.
6. 7-Zip archive probe and listing.
7. Multipart archive grouping and wait states.
8. Secure staged extraction and archive-bomb protection.
9. Unified post-processing controller.
10. Recovery and regression tests for every media/archive failure mode.

## Wave 4 — Finished product experience and hardening

1. Final downloads, queues and categories UI.
2. Task inspector: overview, connections, request, queue, files, post-processing and logs.
3. Complete context actions including Repair Download Link.
4. Local API authentication and origin restrictions.
5. Secure LAN pairing and read-only mode.
6. Structured diagnostics and export.
7. Database backup, migration and repair.
8. Disk-space, missing-file and safe-shutdown handling.
9. Full application battle-test armoury.
10. Fresh-checkout final runtime proof with no Builder-specific workaround.

## Final completion gate

Lumi is complete only when a fresh source checkout can:

- start the application;
- connect the browser extension;
- capture and resolve sources;
- queue, pause, restart and resume downloads safely;
- finish HTTP, video, torrent and archive workflows;
- expose no dead controls or placeholder functions;
- pass the runtime armoury, Sergeant and independent review;
- require the Builder only for packaging, signing and installation.
