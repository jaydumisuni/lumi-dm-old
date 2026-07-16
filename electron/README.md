Electron wrapper — Lumi DM

Requirements
- Node.js (npm)
- Python 3 installed (or set env `LUMIDM_PYTHON` to python executable)

Quick start (development)

```bash
cd electron
npm install
npm run start
```

Notes
- The Electron main process spawns `python server.py` from the project root and waits for `http://127.0.0.1:7000` to respond. For production builds bundle a Python runtime (PyInstaller) or provide instructions for users.
- Build with `npm run build` (electron-builder). Configure signing and publishing per platform.
