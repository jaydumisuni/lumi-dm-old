# Lumi DM — Build Guide

Build once, run anywhere. The server is a Python Flask app. All platforms follow the same pattern:
**Install deps → run with Python** or **package with PyInstaller** for a self-contained executable.

---

## 1. Run from source (all platforms)

```bash
cd "D:\LUMI DM"
pip install -r requirements.txt
python server.py
```

Open **http://localhost:7000** in your browser.

For LAN / mobile access (so your phone can use it too):

```bash
python server.py --host 0.0.0.0 --port 7000
```

Then open `http://<your-PC-IP>:7000` on any device on your network.

---

## 2. Optional features

| Feature | How to enable |
|---|---|
| Torrent / magnet links | `pip install lbry-libtorrent` OR install [aria2c](https://aria2.github.io) and add to PATH |
| Video downloads (YouTube etc.) | `pip install yt-dlp` |
| FFmpeg (for audio extraction) | Install [FFmpeg](https://ffmpeg.org) and add to PATH |

---

## 3. Windows — standalone .exe

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole `
  --add-data "static;static" `
  --name "Lumi-DM" `
  server.py
```

Output: `dist\Lumi-DM.exe`

Double-click to run. Opens at `http://localhost:7000` — open that in your browser.

**Optional: Auto-open browser on launch**

Add to the bottom of `server.py` before `app.run(...)`:

```python
import webbrowser, threading
threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
```

## Electron (Desktop: Windows, macOS, Linux)

You can package the web UI + Python server into a desktop app using Electron. This repository includes a small Electron scaffold in the `electron/` folder.

Quick dev run (requires Node and Python):

```bash
cd electron
npm install
npm run start
```

What the Electron wrapper does:
- Spawns `python server.py` from the project root (use `LUMIDM_PYTHON` env var to point to a custom Python executable).
- Waits for `http://127.0.0.1:7000` to respond, then loads the UI inside the native window.

Packaging:

1. Build or include a Python runtime. Recommended: produce a single-file server binary with PyInstaller (see section 3) and place it beside `server.py` before running the Electron pack.
2. Run `npm run build` inside `electron/` (config uses electron-builder). Configure signing options for macOS/Windows as needed.

Notes:
- Electron apps are larger but fast to ship. For smaller binaries consider Tauri.

---

## 4. macOS — standalone .app

```bash
pip install pyinstaller
pyinstaller --onefile --windowed \
  --add-data "static:static" \
  --name "Lumi-DM" \
  server.py
```

Output: `dist/Lumi-DM`

**Wrap as .app (optional):**

```bash
# Install create-dmg: brew install create-dmg
mkdir -p Lumi-DM.app/Contents/MacOS
cp dist/Lumi-DM Lumi-DM.app/Contents/MacOS/
create-dmg Lumi-DM.dmg Lumi-DM.app
```

---

## 5. Linux — binary or AppImage

```bash
pip install pyinstaller
pyinstaller --onefile \
  --add-data "static:static" \
  --name "Lumi-DM" \
  server.py
```

Output: `dist/Lumi-DM`

Make executable and run:
```bash
chmod +x dist/Lumi-DM
./dist/Lumi-DM
```

**AppImage (distributable):**

```bash
pip install appimage-builder
# Create AppDir structure, then:
appimage-builder --recipe AppImageBuilder.yml
```

---

## 6. Android — PWA (Progressive Web App)

No build needed. Run the server on your PC with `--host 0.0.0.0`.

On your Android phone:
1. Connect phone to same Wi-Fi as PC
2. Open Chrome, go to `http://<PC-IP>:7000`
3. Tap the **⋮ menu → Add to Home Screen**
4. It installs like an app — full screen, works offline for the UI

The phone can then queue downloads that will save to your PC's Downloads folder.

---

## 7. Browser Extension — Chrome / Edge

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `browser-extension/` folder

Done. You'll see the Lumi DM icon in your toolbar.

**Add icons** (required for Chrome store submission):
- Create 16×16, 48×48, 128×128 PNG icons
- Save as `browser-extension/icons/icon16.png`, `icon48.png`, `icon128.png`
- For testing without icons, create blank PNGs: `python -c "from PIL import Image; Image.new('RGBA',(128,128),(79,158,248,255)).save('browser-extension/icons/icon128.png')"`

**For Firefox:**
- Same folder, same manifest.json (MV3 is supported in Firefox 109+)
- Go to `about:debugging#/runtime/this-firefox`
- Click **Load Temporary Add-on** → select `browser-extension/manifest.json`

**Publishing to Chrome Web Store:**
1. Zip the `browser-extension/` folder
2. Go to [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole)
3. Pay one-time $5 developer fee
4. Upload zip, fill in description, submit for review

**Publishing to Firefox Add-ons:**
1. Zip the `browser-extension/` folder
2. Go to [addons.mozilla.org/developers](https://addons.mozilla.org/developers/)
3. Submit → Upload zip → Follow the review process

---

## 8. Change the default download folder

**At runtime:**
```bash
set LUMIDM_DOWNLOAD_DIR=D:\MyDownloads
python server.py
```

**Or in code** — edit `server.py` line:
```python
DEFAULT_DIR = Path(os.environ.get("LUMIDM_DOWNLOAD_DIR", str(Path.home() / "Downloads")))
```

---

## 9. Run as a Windows service (always-on)

Install [NSSM](https://nssm.cc):
```powershell
nssm install LumiDM "C:\Python311\python.exe" "D:\LUMI DM\server.py --host 0.0.0.0"
nssm start LumiDM
```

The DM will start automatically on boot and be available at `http://localhost:7000`.

---

## Project structure

```
D:\LUMI DM\
├── server.py              ← Flask API + static file server
├── requirements.txt       ← Python dependencies
├── core/
│   ├── engine.py          ← HTTP/torrent/video download engine
│   └── grabber.py         ← Web page link scanner
├── static/
│   ├── index.html         ← Web UI
│   ├── app.js             ← Frontend JavaScript
│   └── app.css            ← Styles
└── browser-extension/
    ├── manifest.json      ← Chrome/Firefox MV3 manifest
    ├── background.js      ← Service worker (context menus, intercept)
    ├── popup.html/js      ← Extension popup UI
    ├── content.js         ← Page scanner + torrent intercept
    └── icons/             ← 16×16, 48×48, 128×128 PNGs (add yours)
```

  ## Quick packaging steps

  1. Build the server into a single binary (recommended) so desktop wrappers don't require Python installed.

  On Windows (PowerShell):

  ```powershell
  .
  cd "D:\LUMI DM"
  scripts\build_server.ps1 -Name Lumi-DM
  ```

  On Linux/macOS:

  ```bash
  cd "D:/LUMI DM"
  ./scripts/build_server.sh Lumi-DM
  ```

  2. Use the Electron wrapper in `electron/` to produce desktop installers. From `electron/` run:

  ```bash
  npm install
  npm run build
  ```

  The Electron packaging expects the server binary (or `server.py` + Python) to be present in the project root so the wrapper can spawn it.

