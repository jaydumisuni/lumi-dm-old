const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('lumiSetup', {
  data: () => ipcRenderer.invoke('v5-setup-data'),
  pickFolder: () => ipcRenderer.invoke('v5-setup-pick-folder'),
  confirm: (value) => ipcRenderer.invoke('v5-setup-confirm', value),
  useBrowser: () => ipcRenderer.invoke('v5-setup-browser'),
  cancel: () => ipcRenderer.invoke('v5-setup-cancel'),
});
