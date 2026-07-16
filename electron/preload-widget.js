const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('electronWidget', {
  showMain: () => ipcRenderer.send('widget-show-main'),
});
