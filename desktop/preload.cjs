const { contextBridge, ipcRenderer } = require('electron')

// The only bridge intentionally exposes one-way model-key setup. The React
// page can ask whether a provider exists and submit a new key, but can NEVER
// retrieve any stored key, invoke the shell, or access files.
contextBridge.exposeInMainWorld('ohmworkDesktop', Object.freeze({
  providerState: () => ipcRenderer.invoke('desktop:provider-state'),
  saveProviderKey: (name, value) => ipcRenderer.invoke(
    'desktop:save-provider-key', name, value),
  saveProviderKeys: (values) => ipcRenderer.invoke('desktop:save-provider-keys', values),
}))
