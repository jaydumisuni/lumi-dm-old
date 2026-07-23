const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('lumiWidget', {
  snapshot: () => ipcRenderer.invoke('v5-widget-snapshot'),
  toggleExpanded: () => ipcRenderer.invoke('v5-widget-toggle'),
  action: (action, taskId = '') => ipcRenderer.invoke('v5-widget-action', action, taskId),
  showMain: () => ipcRenderer.send('v5-widget-show-main'),
  onExpanded: (callback) => {
    const listener = (_event, value) => callback(Boolean(value));
    ipcRenderer.on('v5-expanded', listener);
    return () => ipcRenderer.removeListener('v5-expanded', listener);
  },
  onSettings: (callback) => {
    const listener = (_event, value) => callback(value || {});
    ipcRenderer.on('v5-settings-changed', listener);
    return () => ipcRenderer.removeListener('v5-settings-changed', listener);
  },
});
