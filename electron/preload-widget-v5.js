const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('lumiWidget', {
  snapshot: async () => {
    const [snapshot, capacity] = await Promise.all([
      ipcRenderer.invoke('v5-widget-snapshot'),
      ipcRenderer.invoke('v6-capacity-status').catch(() => ({ state: 'idle', result: null })),
    ]);
    return { ...(snapshot || {}), capacity };
  },
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
  onCapacity: (callback) => {
    const listener = (_event, value) => callback(value || {});
    ipcRenderer.on('v6-capacity-status', listener);
    return () => ipcRenderer.removeListener('v6-capacity-status', listener);
  },
});
