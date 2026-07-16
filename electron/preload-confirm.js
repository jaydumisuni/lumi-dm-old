const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('confirmApp', {
  getJobData:    ()  => ipcRenderer.invoke('request-job-data'),
  pickFolder:    ()  => ipcRenderer.invoke('pick-folder'),
  confirmStaged: (jobId, filename, targetDir, remember) =>
    ipcRenderer.invoke('confirm-staged', jobId, filename, targetDir, remember),
  cancelStaged:  (jobId) => ipcRenderer.invoke('cancel-staged', jobId),
  close:         ()  => ipcRenderer.send('confirm-close'),
});
