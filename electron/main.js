const {
  app, BrowserWindow, shell, dialog, Menu, Tray, nativeImage,
  screen, ipcMain, Notification,
} = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

// Must be set before app 'ready' — prevents "electron.app.*" in notifications
app.setName('Lumi DM');
if (process.platform === 'win32') {
  app.setAppUserModelId('com.lumi.dm');
}

let mainWindow   = null;
let widgetWindow = null;
let tray         = null;
let serverProc   = null;
let isQuitting   = false;

const WINDOWS_LOGIN_ARGS = ['--hidden', '--login-startup'];
const WINDOWS_LEGACY_LOGIN_ARGS = ['--hidden'];

// Speed / status cache updated by pollServer()
const _prevStatus   = {};
let _lastSpeed      = 0;
let _lastActive     = 0;

function getWindowsLoginItemOptions(args = WINDOWS_LOGIN_ARGS) {
  return { path: process.execPath, args };
}

function getWindowsLoginItemState(args = WINDOWS_LOGIN_ARGS) {
  return app.getLoginItemSettings(getWindowsLoginItemOptions(args));
}

function isHiddenLaunch(argv = process.argv) {
  return argv.includes('--hidden') || argv.includes('--login-startup');
}

function isStartupLaunch(argv = process.argv) {
  if (process.platform === 'win32') return isHiddenLaunch(argv);
  const settings = app.getLoginItemSettings();
  return settings.wasOpenedAtLogin || settings.wasOpenedAsHidden || isHiddenLaunch(argv);
}

function isLoginItemEnabled(settings) {
  return settings.openAtLogin && settings.enabled !== false;
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    if (isHiddenLaunch(argv)) return;
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      hideWidget();
      return;
    }
    createWindow(false);
  });
}

// ── Python server ─────────────────────────────────────────────────────────────

function _killPort7000() {
  // Kill any leftover server from a previous Electron session that didn't exit cleanly
  if (process.platform === 'win32') {
    try {
      const out = require('child_process').execSync(
        'netstat -ano | findstr ":7000 " | findstr "LISTENING"',
        { encoding: 'utf8', timeout: 3000 }
      );
      for (const line of out.trim().split(/\r?\n/)) {
        const pid = line.trim().split(/\s+/).pop();
        if (/^\d+$/.test(pid) && pid !== '0') {
          require('child_process').spawnSync('taskkill', ['/PID', pid, '/F', '/T']);
        }
      }
    } catch (_) {}
  } else {
    try {
      require('child_process').execSync('fuser -k 7000/tcp', { timeout: 3000 });
    } catch (_) {}
  }
}

function startPythonServer() {
  _killPort7000();
  let cmd, args;
  const env = Object.assign({}, process.env);

  if (app.isPackaged) {
    const ext = process.platform === 'win32' ? '.exe' : '';
    cmd = path.join(process.resourcesPath, 'server', `LUMIDM-server${ext}`);
    args = ['--host', '127.0.0.1', '--port', '7000'];
    env.LUMIDM_STATIC_DIR = path.join(process.resourcesPath, 'static');
    env.LUMIDM_DATA_DIR   = app.getPath('userData');
  } else {
    const script = path.resolve(__dirname, '..', 'server.py');
    cmd = process.env.LUMIDM_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    args = [script, '--host', '127.0.0.1', '--port', '7000'];
  }

  try {
    serverProc = spawn(cmd, args, { stdio: 'ignore', env });
    serverProc.on('error', (err) => console.error('Server failed to start:', err));
    serverProc.on('exit',  (code) => console.log('Server exited', code));
  } catch (e) {
    console.error('Failed to spawn server:', e);
  }
}

function stopPythonServer() {
  if (!serverProc || serverProc.killed) return;
  try {
    if (process.platform === 'win32') {
      // taskkill /F /T kills the entire process tree on Windows (SIGTERM is unreliable)
      require('child_process').spawnSync('taskkill', ['/PID', String(serverProc.pid), '/F', '/T']);
    } else {
      serverProc.kill('SIGTERM');
    }
  } catch (_) {}
}

function waitForServer(url, cb, timeout = 25000) {
  const start = Date.now();
  const tryOnce = () => {
    http.get(url, () => cb(true)).on('error', () => {
      if (Date.now() - start > timeout) cb(false);
      else setTimeout(tryOnce, 250);
    });
  };
  tryOnce();
}

// ── Prefs ─────────────────────────────────────────────────────────────────────

const EXT_DIR    = app.isPackaged
  ? path.join(process.resourcesPath, 'browser-extension')
  : path.resolve(__dirname, '..', 'browser-extension');
const PREFS_FILE = path.join(app.getPath('userData'), 'LUMIDM-prefs.json');

function loadPrefs() {
  try { return JSON.parse(fs.readFileSync(PREFS_FILE, 'utf8')); } catch { return {}; }
}
function savePrefs(data) {
  try { fs.writeFileSync(PREFS_FILE, JSON.stringify(data), 'utf8'); } catch {}
}

// ── Startup at login ──────────────────────────────────────────────────────────

function getStartupEnabled() {
  if (process.platform === 'linux') return loadPrefs().startAtLogin === true;
  if (process.platform === 'win32') {
    const current = getWindowsLoginItemState();
    if (current.openAtLogin) return isLoginItemEnabled(current);
    return isLoginItemEnabled(getWindowsLoginItemState(WINDOWS_LEGACY_LOGIN_ARGS));
  }
  return app.getLoginItemSettings().openAtLogin;
}

function setStartupEnabled(enable) {
  if (process.platform === 'linux') {
    const p = loadPrefs(); p.startAtLogin = enable; savePrefs(p);
  } else if (process.platform === 'win32') {
    app.setLoginItemSettings({
      ...getWindowsLoginItemOptions(WINDOWS_LEGACY_LOGIN_ARGS),
      openAtLogin: false,
    });
    app.setLoginItemSettings({
      ...getWindowsLoginItemOptions(),
      openAtLogin: enable,
    });
  } else {
    app.setLoginItemSettings({ openAtLogin: enable, openAsHidden: true, args: ['--hidden'] });
  }
  rebuildTrayMenu();
  buildMenu();
}

// ── Speed helpers ─────────────────────────────────────────────────────────────

function fmtSpeed(bps) {
  if (bps >= 1048576) return (bps / 1048576).toFixed(1) + ' MB/s';
  if (bps >= 1024)    return (bps / 1024).toFixed(0)    + ' KB/s';
  return bps.toFixed(0) + ' B/s';
}

// ── Server polling (completion notifications + tray tooltip + widget) ─────────

function pollServer() {
  const req = http.get('http://127.0.0.1:7000/api/downloads?limit=100', (res) => {
    let raw = '';
    res.on('data', d => raw += d);
    res.on('end', () => {
      try {
        const { downloads = [] } = JSON.parse(raw);
        _lastActive = downloads.filter(j => j.status === 'running').length;
        _lastSpeed  = downloads.reduce((s, j) => s + (+j.speed_bytes_per_sec || 0), 0);

        // Update tray tooltip with live speed
        if (tray) {
          tray.setToolTip(_lastActive > 0
            ? `Lumi DM  ↓ ${fmtSpeed(_lastSpeed)}  (${_lastActive} active)`
            : 'Lumi DM');
        }

        // Completion notifications + staged-download alert
        let hasNewStaged = false;
        downloads.forEach(j => {
          if (j.status === 'completed' && _prevStatus[j.id] !== 'completed') {
            notifyComplete(j);
          }
          if (j.status === 'staged' && _prevStatus[j.id] !== 'staged') {
            hasNewStaged = true;
          }
          _prevStatus[j.id] = j.status;
        });

        // Bring main window forward when a staged download needs confirmation
        if (hasNewStaged) showMainWindowForStaged();

        // Taskbar progress bar
        if (mainWindow && !mainWindow.isDestroyed()) {
          const running = downloads.filter(j => j.status === 'running');
          if (running.length > 0) {
            const avg = running.reduce((s, j) => s + (j.progress_percent || 0), 0) / running.length;
            mainWindow.setProgressBar(avg / 100);
          } else {
            mainWindow.setProgressBar(-1);
          }
        }

        // Remove stale IDs from cache
        const cur = new Set(downloads.map(j => j.id));
        Object.keys(_prevStatus).forEach(id => { if (!cur.has(id)) delete _prevStatus[id]; });
      } catch {}
    });
  }).on('error', () => {});
  req.setTimeout(5000, () => req.destroy());
}

function notifyComplete(job) {
  if (!Notification.isSupported()) return;
  const n = new Notification({
    title: 'Download complete',
    body:  job.filename || 'File downloaded',
    icon:  getIconPath(),
    silent: false,
  });
  n.on('click', () => {
    // Open file in Explorer/Finder
    http.request(
      { hostname: '127.0.0.1', port: 7000, path: `/api/downloads/${job.id}/open`, method: 'POST', headers: { 'Content-Length': 0 } },
      () => {}
    ).on('error', () => {}).end();
    if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
    else createWindow();
    hideWidget();
  });
  n.show();
}

// ── Mini corner widget ────────────────────────────────────────────────────────

function createWidget() {
  if (widgetWindow && !widgetWindow.isDestroyed()) { widgetWindow.show(); return; }
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  widgetWindow = new BrowserWindow({
    width: 220, height: 60,
    x: width - 232, y: height - 72,
    frame: false, transparent: true, hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, resizable: false,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, 'preload-widget.js'),
    },
  });
  widgetWindow.loadFile(path.join(__dirname, 'widget.html'));
  widgetWindow.on('closed', () => { widgetWindow = null; });
}

function showWidget() { createWidget(); }

function hideWidget() {
  if (widgetWindow && !widgetWindow.isDestroyed()) {
    widgetWindow.close();
  }
}

// ── IDM-style mini confirm popup ──────────────────────────────────────────────

// Show main window when a staged download needs confirmation
function showMainWindowForStaged() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (widgetWindow && !widgetWindow.isDestroyed()) widgetWindow.hide();
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.focus();
}

// IPC: renderer signals it finished confirming/cancelling — hide back to widget
ipcMain.on('staged-confirm-done', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.hide();
  showWidget();
});

// ── Clipboard monitor — offer to download copied URLs ─────────────────────────

const { clipboard } = require('electron');
const _DL_CLIP_RE   = /\.(zip|rar|7z|exe|msi|dmg|apk|pdf|iso|mp4|mkv|mp3|flac|torrent|tar\.gz|tar\.bz2)(\?.*)?$/i;
let   _lastClip     = '';

function checkClipboard() {
  try {
    const text = clipboard.readText().trim();
    if (text === _lastClip || text.length < 10) return;
    _lastClip = text;
    const isMagnet = text.startsWith('magnet:');
    const isUrl    = text.startsWith('http://') || text.startsWith('https://');
    if ((isUrl && _DL_CLIP_RE.test(text)) || isMagnet) {
      const fakeJob = {
        id:          '__clipboard__',
        url:         text,
        filename:    isMagnet ? 'Magnet link' : text.split('/').pop().split('?')[0],
        total_bytes: 0,
        target_dir:  '',
        status:      'staged',
        _fromClipboard: true,
      };
      showMainWindowForStaged();
    }
  } catch (_) {}
}
setInterval(checkClipboard, 600);

// IPC: renderer requests its job data keyed by window ID (safe with multiple concurrent popups)

// IPC: native folder picker (main window and confirm popup both use this)
ipcMain.handle('pick-folder', async (e) => {
  const win    = BrowserWindow.fromWebContents(e.sender) || mainWindow || BrowserWindow.getFocusedWindow();
  const result = await dialog.showOpenDialog(win, {
    title:      'Choose download folder',
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
});

// Promisified http POST helper (avoids fetch() which may not exist in all Electron versions)
function httpPost(path, bodyObj) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(bodyObj);
    const req = http.request(
      { hostname: '127.0.0.1', port: 7000, path, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } },
      (res) => {
        let raw = '';
        res.on('data', (c) => { raw += c; });
        res.on('end', () => {
          try { resolve(JSON.parse(raw)); } catch { resolve({}); }
        });
      }
    );
    req.on('error', reject);
    req.end(payload);
  });
}

// IPC: confirm a staged download from the mini popup
ipcMain.handle('confirm-staged', async (_, jobId, filename, targetDir, remember) => {
  try {
    const data = await httpPost(`/api/downloads/${jobId}/confirm`, { filename, target_dir: targetDir });
    if (remember && targetDir) {
      httpPost('/api/settings/default-dir', { dir: targetDir }).catch(() => {});
    }
    return data;
  } catch (e) { return { error: e.message }; }
});

// IPC: cancel/delete a staged download from the mini popup
ipcMain.handle('cancel-staged', async (_, jobId) => {
  if (jobId === '__clipboard__') return { ok: true };
  try {
    await httpPost(`/api/downloads/${jobId}/delete`, { delete_file: false });
    return { ok: true };
  } catch (e) { return { error: e.message }; }
});

// IPC: confirm popup self-close
ipcMain.on('confirm-close', (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) win.close();
});

// IPC from widget: bring main window to front
ipcMain.on('widget-show-main', () => {
  if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
  else createWindow();
  hideWidget();
});

// ── System tray ───────────────────────────────────────────────────────────────

function getIconPath() {
  if (process.platform === 'win32') {
    if (app.isPackaged) {
      return path.join(process.resourcesPath, 'assets', 'windows', 'Lumi-DM.ico');
    }
    return path.join(__dirname, '..', 'assets', 'windows', 'Lumi-DM.ico');
  }

  const name = 'favicon-256.png';
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'static', name);
  }
  return path.join(__dirname, '..', 'static', name);
}

function rebuildTrayMenu() {
  if (!tray) return;
  const startEnabled = getStartupEnabled();
  const ctx = Menu.buildFromTemplate([
    { label: 'Lumi DM', enabled: false },
    { type: 'separator' },
    {
      label: 'Show',
      click: () => {
        if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
        else createWindow();
        hideWidget();
      },
    },
    { type: 'separator' },
    {
      label: 'Run at Windows startup',
      type: 'checkbox',
      checked: startEnabled,
      click: (item) => setStartupEnabled(item.checked),
    },
    { type: 'separator' },
    { label: 'Quit', click: () => { isQuitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(ctx);
}

function createTray() {
  const icon = nativeImage.createFromPath(getIconPath());
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('Lumi DM');

  // Single click → show speed balloon
  tray.on('click', () => {
    if (process.platform === 'win32') {
      tray.displayBalloon({
        title:    'Lumi DM',
        content:  _lastActive > 0
          ? `${_lastActive} active  ·  ↓ ${fmtSpeed(_lastSpeed)}`
          : `↓ ${fmtSpeed(_lastSpeed)}  —  No active downloads`,
        iconType: 'info',
      });
    } else {
      // macOS / Linux: open main window on click
      if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
      else createWindow();
      hideWidget();
    }
  });

  // Double click → open main window
  tray.on('double-click', () => {
    if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
    else createWindow();
    hideWidget();
  });

  rebuildTrayMenu();
}

// ── Extension prompt ──────────────────────────────────────────────────────────

function showExtensionPrompt() {
  const prefs = loadPrefs();
  if (prefs.extPromptDismissed) return;
  dialog.showMessageBox(mainWindow, {
    type: 'info',
    title: 'Install the Browser Extension',
    message: 'Get one-click downloads from any webpage',
    detail:
      'The Lumi DM browser extension lets you right-click any link, ' +
      'torrent, or video URL and send it directly to this app.\n\n' +
      'Chrome / Edge / Brave:\n' +
      '  1. Open chrome://extensions\n' +
      '  2. Enable Developer mode\n' +
      '  3. Click "Load unpacked" → select the browser-extension folder\n\n' +
      'Firefox:\n' +
      '  1. Open about:debugging → This Firefox\n' +
      '  2. Load Temporary Add-on → select browser-extension/manifest.json',
    buttons: ['Open Extension Folder', 'Not Now', "Don't Ask Again"],
    defaultId: 0, cancelId: 1,
  }).then(({ response }) => {
    if (response === 0) shell.openPath(EXT_DIR);
    if (response === 2) { const p = loadPrefs(); p.extPromptDismissed = true; savePrefs(p); }
  });
}

function maybeShowExtensionPrompt() {
  if (!mainWindow || mainWindow.isDestroyed() || !mainWindow.isVisible()) return;
  showExtensionPrompt();
}

// ── App menu ──────────────────────────────────────────────────────────────────

function buildMenu() {
  const startEnabled = getStartupEnabled();
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'Open Downloads Folder', click: () => shell.openPath(app.getPath('downloads')) },
        { type: 'separator' },
        {
          label: 'Run at Startup', type: 'checkbox', checked: startEnabled,
          click: (item) => setStartupEnabled(item.checked),
        },
        { type: 'separator' },
        { label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => { isQuitting = true; app.quit(); } },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' }, { role: 'forceReload' },
        { type: 'separator' }, { role: 'toggleDevTools' },
        { type: 'separator' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' }, { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'Install Browser Extension',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info', title: 'Install Browser Extension',
              message: 'Add the extension to your browser',
              detail:
                'Chrome / Edge / Brave:\n' +
                '  1. Go to chrome://extensions\n' +
                '  2. Enable Developer mode\n' +
                '  3. Load unpacked → select the browser-extension folder\n\n' +
                'Firefox:\n' +
                '  1. Go to about:debugging → This Firefox\n' +
                '  2. Load Temporary Add-on → select manifest.json',
              buttons: ['Open Extension Folder', 'Close'], defaultId: 0,
            }).then(({ response }) => { if (response === 0) shell.openPath(EXT_DIR); });
          },
        },
        { type: 'separator' },
        { label: 'Open Extension Folder', click: () => shell.openPath(EXT_DIR) },
      ],
    },
  ]));
}

// ── Main window ───────────────────────────────────────────────────────────────

function createWindow(startHidden = false) {
  mainWindow = new BrowserWindow({
    width: 820, height: 580, minWidth: 680, minHeight: 460,
    center: true, show: !startHidden,
    title: 'Reminal Download Manager',
    icon: getIconPath(),
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, 'preload-main.js'),
    },
    backgroundColor: '#111317',
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Minimize → show mini widget
  mainWindow.on('minimize', () => showWidget());

  // Restore / focus → hide mini widget
  mainWindow.on('restore', () => hideWidget());
  mainWindow.on('focus',   () => hideWidget());

  // Close → hide to tray + show mini widget
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow.hide();
      showWidget();
      if (tray && process.platform === 'win32') {
        const p = loadPrefs();
        if (!p.trayBalloonShown) {
          tray.displayBalloon({
            title:    'Lumi DM',
            content:  'Running in the background. Right-click the tray icon to quit.',
            iconType: 'info',
          });
          savePrefs({ ...p, trayBalloonShown: true });
        }
      }
    }
  });

  mainWindow.on('show', () => hideWidget());
  mainWindow.on('closed', () => { mainWindow = null; });

  if (startHidden) {
    mainWindow.once('show', () => setTimeout(maybeShowExtensionPrompt, 750));
  }

  const serverUrl = 'http://127.0.0.1:7000';
  waitForServer(serverUrl, (ok) => {
    if (ok) {
      mainWindow.loadURL(serverUrl);
    } else {
      const staticDir = app.isPackaged
        ? path.join(process.resourcesPath, 'static')
        : path.join(__dirname, '..', 'static');
      mainWindow.loadFile(path.join(staticDir, 'index.html'));
    }
    if (!startHidden) {
      setTimeout(maybeShowExtensionPrompt, 3000);
    }
  });
}

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.on('ready', () => {
  if (!gotSingleInstanceLock) return;
  buildMenu();
  createTray();
  startPythonServer();
  const startHidden = isStartupLaunch();
  createWindow(startHidden);
  // Start polling server 4 s after launch so server has time to boot
  setTimeout(() => setInterval(pollServer, 2000), 4000);
});

app.on('before-quit', () => {
  isQuitting = true;
  stopPythonServer();
});

app.on('window-all-closed', () => {
  // Keep running in tray — user must quit via tray menu or File > Quit
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
  else { mainWindow.show(); mainWindow.focus(); hideWidget(); }
});
