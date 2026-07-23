const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronApp', {
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  isElectron: true,
  getDesktopSettings: () => ipcRenderer.invoke('v5-desktop-settings-get'),
  saveDesktopSettings: (value) => ipcRenderer.invoke('v5-desktop-settings-save', value),
  showWidget: () => ipcRenderer.send('v5-widget-show'),
  checkForUpdates: (manual = false) => ipcRenderer.invoke('v5-update-check', manual),
  getConnectionCapacity: () => ipcRenderer.invoke('v6-capacity-status'),
  runConnectionCapacityTest: () => ipcRenderer.invoke('v6-capacity-run'),
  windowControl: (action) => ipcRenderer.invoke('ttg-window-control', action),
  getWindowState: () => ipcRenderer.invoke('ttg-window-state'),
  getAppInfo: () => ipcRenderer.invoke('ttg-app-info'),
  onWindowState: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, value) => callback(value || {});
    ipcRenderer.on('ttg-window-state-changed', listener);
    return () => ipcRenderer.removeListener('ttg-window-state-changed', listener);
  },
  onUpdateStatus: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('v5-update-status', listener);
    return () => ipcRenderer.removeListener('v5-update-status', listener);
  },
  onConnectionCapacity: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, value) => callback(value || {});
    ipcRenderer.on('v6-capacity-status', listener);
    return () => ipcRenderer.removeListener('v6-capacity-status', listener);
  },
  onServerState: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, value) => callback(value || {});
    ipcRenderer.on('lumi-server-state', listener);
    return () => ipcRenderer.removeListener('lumi-server-state', listener);
  },
});
