# Running Lumi DM From Source

Lumi is proved as a working application before any external packaging system is
allowed to touch it.

## Start the runtime

```bash
python -m venv .venv
```

Activate the environment, then install the application dependencies:

```bash
python -m pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:7000`.

The first loopback browser session receives a local owner token. Other devices
must use a one-time pairing code created from **Browser & LAN**.

## Browser extension

Load `browser-extension/` as an unpacked extension in a Chromium-compatible
browser. The service worker authenticates only to the configured loopback Lumi
API and never adds Lumi credentials to unrelated network requests.

## Optional runtime tools

Lumi discovers these at runtime when their functions are used:

- FFmpeg and ffprobe for media merging and conversion;
- 7-Zip/7zz for archive inspection, testing and extraction;
- aria2c or libtorrent for torrent transfers;
- yt-dlp through `requirements.txt` for media providers.

They may be available on `PATH` or under a local `tools/` runtime directory.
Their packaging and version selection belong to the external Builder.

## Proof suite

```bash
python -m compileall -q core server.py tests
python -m pytest -q
```

The permanent tests cover HTTP range correctness, queues, crash recovery,
browser request replay, media and torrent flows, archive safety, authentication,
backups, diagnostics, product UI contracts and fresh-source startup.

## Desktop shell

`electron/` contains only the Lumi desktop-shell source and its entry-point
metadata. It intentionally contains no installer, signing, icon-conversion or
self-packaging machinery. The external Builder supplies the Electron runtime and
creates final desktop artifacts from this proven source.
