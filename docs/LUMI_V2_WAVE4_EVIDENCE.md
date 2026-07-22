# Lumi DM V2 — Wave 4 Evidence Packet

Wave 4 turns the proven source runtime into the finished Lumi product. The external
Software Builder remains outside this work: Lumi must start, operate, recover and
prove every visible function directly from a clean source checkout.

## Ten implementation fronts

1. Final responsive application UI for overview, downloads, queues, categories,
   LinkGrabber, settings and diagnostics.
2. Seven-tab task inspector for overview, connections, request, queue, files,
   post-processing and event history.
3. Real task context actions: pause, resume, retry, cancel, Repair Download Link,
   queue/priority changes, open, move/rename, verify, extract, locate and remove.
4. Authenticated local API sessions, same-origin write protection and restricted
   extension CORS.
5. One-time secure pairing for browser/LAN clients with owner or read-only roles,
   hashed persistent tokens and immediate revocation.
6. Privacy-safe diagnostics summaries and ZIP exports with credentials, query
   strings and private filesystem locations removed.
7. Database integrity checks, online backups, recovery exports and guarded
   checkpoint/reindex/analyse/vacuum repair.
8. Storage health, missing-completed-file detection, location repair, startup
   backups and safe runtime shutdown.
9. Security, role, maintenance, diagnostics, JavaScript and extension regression
   tests.
10. Fresh-checkout proof that launches `python server.py`, authenticates over HTTP,
    creates a task, moves its real file, opens its inspector, exports diagnostics,
    backs up the database and shuts down cleanly.

## Product truth rules

- A visible control must call a working backend operation.
- Read-only clients may inspect but cannot modify application state.
- Browser credentials are sent only to the exact configured Lumi origin.
- Ordinary logs and diagnostic exports never reveal passwords, cookies,
  authorization headers, tokens, private query strings or personal paths.
- Database repair always creates a backup first.
- The Builder is needed only after Lumi already runs correctly from source.

## Review closure

Wave 4 must pass the complete runtime armoury, JavaScript/extension syntax proof,
fresh-checkout product test, Sergeant primary review and independent witness
review. CodeRabbit remains an external gate and must not be claimed as passed
while its service is rate-limited.
