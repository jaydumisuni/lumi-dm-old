const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronApp', {
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  isElectron: true,
  afterStagedConfirm: () => ipcRenderer.send('staged-confirm-done'),
});
