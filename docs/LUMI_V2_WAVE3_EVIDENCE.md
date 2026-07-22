# Lumi DM V2 — Wave 3 Evidence Packet

Wave 3 starts from the proven Wave 2 head. Lumi remains source-runnable; packaging
and installer generation are not part of this mission.

## Ten implementation fronts

1. Detailed yt-dlp media and playlist resolution.
2. Format, subtitle, thumbnail, metadata and audio-only selection.
3. Recoverable yt-dlp transfer state and progress.
4. Torrent metadata parsing and info-hash evidence.
5. Torrent file selection, priorities, peers, ratio and seeding controls.
6. 7-Zip archive listing and integrity testing.
7. Multipart archive grouping and missing-volume wait states.
8. Staged extraction with traversal, link, file-count, size and ratio defenses.
9. FFmpeg conversion progress and a unified post-processing controller.
10. Deterministic offline regression tests and source APIs.

## Proof strategy

The test suite creates local fake yt-dlp, aria2c, FFmpeg, ffprobe and 7-Zip
implementations. No public media site, tracker, torrent peer, archive utility or
network service is required to prove control flow and persistence.

## Maintainer hardening findings

- FFmpeg atomic temporary outputs preserve the final container extension so the
  real muxer is selected correctly.
- Pause and cancel remain authoritative even when yt-dlp wraps a progress-hook
  exception in its own error type.
- Playlist completion records all finished files instead of treating the playlist
  dictionary as one output file.
- Torrent metadata is decoded through byte, depth, item, ordering and truncation
  limits, with typed `BencodeError` failures for malformed input.
- The two late Wave 2 request-envelope size regressions are included in this tree.

## Review closure

- Runtime and browser/source proof must pass.
- Sergeant remains the primary engineering reviewer.
- Maintainer and CodeRabbit remain independent witnesses.
- Accepted findings become permanent regressions before merge.
