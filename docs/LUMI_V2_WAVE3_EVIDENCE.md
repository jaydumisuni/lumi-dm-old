# Lumi DM V2 — Wave 3 Evidence Packet

Wave 3 adds media, torrent, archive and post-processing functions to the proven
source-runtime foundation. The external Software Builder remains outside this work.

## Ten implementation fronts

1. Full yt-dlp media inspection with formats, playlists and selectable entries.
2. Subtitle, automatic-caption, thumbnail and metadata controls.
3. FFmpeg merge, remux and audio-extraction jobs with persistent progress.
4. Torrent metadata resolution and selectable files.
5. Torrent file priorities, peers, seeds, rates, ratio and seeding policies.
6. 7-Zip archive inspection, technical listing and integrity tests.
7. Multipart RAR, 7z, ZIP and numbered-volume grouping with missing-part states.
8. Secure staged extraction with traversal, symlink, file-count, size and ratio limits.
9. Unified persistent post-processing jobs with cancellation and failure recovery.
10. Source APIs plus deterministic fake-engine runtime proofs.

## Runtime proof targets

- playlist entries, formats, captions and thumbnails are surfaced;
- yt-dlp progress and FFmpeg postprocessor states reach a completed task;
- torrent files, priorities, peer telemetry, ratio and seeding policy persist;
- pause leaves a resumable torrent task;
- archive gaps become waiting-input states instead of generic failures;
- unsafe paths, links and archive-bomb ratios are rejected before extraction;
- staged 7-Zip extraction never writes directly into the final destination;
- FFmpeg and archive jobs persist inside their parent task;
- verified HTTP archives enter automatic extraction only after final-file completion;
- every Wave 3 endpoint is available from `python server.py`.

## Review closure

The wave must pass runtime proof, Sergeant, maintainer witness review and CodeRabbit.
Every accepted finding becomes a permanent regression before merge.
