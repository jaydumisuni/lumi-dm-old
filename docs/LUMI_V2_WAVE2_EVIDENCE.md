# Lumi DM V2 — Wave 2 Evidence Packet

Wave 2 starts from the proven Wave 1 head. The separate Software Builder remains
out of scope: Lumi must run and prove these functions directly from source.

## Ten implementation fronts

1. Default and custom download categories.
2. Separate category-aware temporary and final folders.
3. Duplicate URL strategies: reuse, reject, rename and overwrite.
4. Per-host connection, speed, User-Agent, proxy, credentials and interception rules.
5. Encrypted local vault for cookies, authorization and POST bodies.
6. Browser force, bypass and persistent per-host interception controls.
7. Browser-assisted Repair Download Link capture.
8. Unified direct, torrent, video and HLS/DASH resolver registry.
9. Complete request replay for GET and POST-generated downloads.
10. Source-runtime Flask APIs plus permanent browser and runtime proofs.

## Security boundary

Captured request secrets may only be sent by the extension to a loopback Lumi
server. Sensitive headers and POST bodies are encrypted before a task enters the
normal SQLite store. Public task views expose only redacted placeholders.

## Runtime proof targets

- encrypted secrets are absent from persisted task JSON;
- encrypted Authorization and Cookie headers replay successfully;
- captured POST bytes reproduce the browser request and complete a download;
- categories select separate final and temporary directories;
- duplicate reuse returns the original task;
- host credentials and rules apply without public secret disclosure;
- special URLs resolve before generic HTTP;
- Repair Link waits for and securely accepts a replacement browser request;
- `python server.py` exposes every Wave 2 API;
- the Manifest V3 background worker, popup and content script pass Node syntax checks.

## Two review gates

1. Sergeant primary engineering review.
2. Maintainer witness plus CodeRabbit independent review.

Accepted findings become permanent regressions before the wave is merged.
