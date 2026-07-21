# Lumi DM

**Lumi DM** — short for **Luminal Download Manager** — is named after *luminal speed*, the speed of light. The name reflects the project's focus on fast, reliable downloads without unnecessary complexity.

Lumi DM is a cross-platform download manager with a Flask backend, web UI, browser extension, Electron desktop wrapper, and native mobile project scaffolds.

## Features

- HTTP, HTTPS, FTP, magnet, torrent, and video download entry points
- Browser extension for sending links directly to the app
- Web UI served by the local Flask server
- Electron desktop packaging for Windows, macOS, and Linux
- Android and iOS project scaffolds

## Run From Source

```bash
pip install -r requirements.txt
python server.py
```

Open `http://localhost:7000`.

For LAN/mobile access:

```bash
python server.py --host 0.0.0.0 --port 7000
```

## Desktop Wrapper

```bash
cd electron
npm install
npm run start
```

See [BUILD.md](BUILD.md) for packaging instructions across platforms.
