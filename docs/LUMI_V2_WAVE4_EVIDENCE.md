# Lumi DM V2 — Wave 4 Product Evidence

Wave 4 turns the proven transfer, capture, media, torrent and archive systems into
a complete source-running product. The external Software Builder remains outside
this mission and receives Lumi only after the runtime is proven.

## Ten implementation fronts

1. Loopback bootstrap authentication and restricted origins.
2. Read, write and owner client scopes.
3. One-time LAN pairing, client listing and revocation.
4. Complete downloads, queues, categories, browser and settings workspace.
5. Seven-tab task inspector with safe task/file actions.
6. Database integrity, filesystem checks and missing-file recovery.
7. Full-state backups, verification and pre-repair snapshots.
8. Sanitized diagnostic evidence with secrets and home paths removed.
9. Safe lifecycle shutdown, bounded payloads and installable static shell.
10. Fresh-source product proofs, browser syntax checks and final review gates.

## Product boundary

- The source application owns all behavior and runs without packaging fixes.
- API state is authenticated; extension request secrets travel only to loopback.
- Read-only paired clients cannot mutate downloads.
- Browser/PWA caching excludes every `/api/` response.
- Public task views stay available even when encrypted replay state is damaged.
- The Builder later packages this proven runtime; it does not complete it.

## Permanent proof targets

- unauthenticated APIs return 401;
- hostile browser origins cannot bootstrap an owner session;
- local owner bootstrap works and replaces stale same-client sessions;
- one-time read-only pairing works and cannot perform POST actions;
- category and temporary-folder choices are applied before queue dispatch;
- task inspection, move, locate and restart modify real files and persistent state;
- backups pass SQLite and ZIP verification;
- diagnostic exports contain no captured secrets;
- repair backs up first and marks missing completed files explicitly;
- the final HTML, PWA and extension are Lumi-branded and contain no dead inline handlers;
- a fresh temporary source runtime imports, authenticates and exposes every product route.

## Review closure

1. Wave 4 product proof.
2. Full Lumi runtime proof.
3. Sergeant primary engineering review.
4. Maintainer witness review.
5. CodeRabbit independent review.

Every accepted finding becomes a permanent regression before integration.
