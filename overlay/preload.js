'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// The renderer is a view: it can read the roster, ask for a reply, and send a
// window command. It gets no Node, no filesystem, and no agent credentials.
contextBridge.exposeInMainWorld('wow', {
  onVisibility: callback => ipcRenderer.on('wow:visibility', (_event, data) => callback(data)),
  queueSessions: (sessions, summary) => ipcRenderer.send('wow:queue-sessions', sessions, summary),
  onRoster: callback => ipcRenderer.on('wow:roster', (_event, data) => callback(data)),
  onMode: callback => ipcRenderer.on('wow:mode', (_event, data) => {
    callback(data)
    requestAnimationFrame(() => requestAnimationFrame(() => ipcRenderer.send('wow:mode-ready', data.epoch)))
  }),
  onFocus: callback => ipcRenderer.on('wow:focus', (_event, data) => callback(data)),
  onLive: callback => ipcRenderer.on('wow:live', (_event, data) => callback(data)),
  onTheme: callback => ipcRenderer.on('wow:theme', (_event, data) => callback(data)),
  onReset: callback => ipcRenderer.on('wow:reset', (_event, data) => callback(data)),
  onDemo: callback => ipcRenderer.on('wow:demo', (_event, data) => callback(data)),
  reply: (sessionId, text) => ipcRenderer.send('wow:reply', { sessionId, text }),
  action: action => ipcRenderer.invoke('wow:action', action),
  chooseDirectory: () => ipcRenderer.invoke('wow:choose-directory'),
  command: command => ipcRenderer.send('wow:command', command),
  select: sessionId => ipcRenderer.send('wow:select', sessionId),
  openExternal: url => ipcRenderer.send('wow:open-external', url),
  badgeWidth: width => ipcRenderer.send('wow:badge-width', width)
})
