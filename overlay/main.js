'use strict'

// Agent Board overlay: one always-on-top window that has two shapes and no
// chrome, plus a unix socket so a keybind can drive it.
//
// Provider state arrives over the private bridge socket. The renderer persists
// unsent messages and drafts; native window positions are saved here. Hyprland
// owns placement/geometry on Wayland, including restoring each window shape.

const { app, BrowserWindow, dialog, ipcMain, nativeTheme, screen, shell } = require('electron')
const { execFile } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')
const { loadOmarchyTheme, themeSignature } = require('./omarchy_theme')

const CLI = process.env.AGENT_BOARD_CLI || path.join(__dirname, '..', 'bin', 'agent-board')
const START_MODE = process.env.AGENT_BOARD_MODE === 'board' ? 'board' : 'badge'
const PIN_ENABLED = process.env.AGENT_BOARD_PIN !== '0'
const GAME_AWARE = process.env.AGENT_BOARD_GAME_AWARE === '1'
const SELF_TEST = process.env.AGENT_BOARD_SELF_TEST === '1'
const HEADLESS = SELF_TEST || process.env.AGENT_BOARD_HEADLESS === '1'
const SELF_TEST_SCREENSHOT = process.env.AGENT_BOARD_SELF_TEST_SCREENSHOT || ''
const REFRESH_MS = Number(process.env.AGENT_BOARD_REFRESH_MS || 4000)
const RUNTIME_DIR = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`
const SOCKET_PATH = path.join(RUNTIME_DIR, 'agent-board.sock')
const LIVE_SOCKET_PATH = process.env.AGENT_BOARD_LIVE_SOCKET || path.join(RUNTIME_DIR, 'agent-board-live.sock')
const LIVE_POLL_MS = Number(process.env.AGENT_BOARD_LIVE_POLL_MS || 750)
const LOG_PATH = path.join(RUNTIME_DIR, 'agent-board.log')
const STATE_HOME = process.env.XDG_STATE_HOME || path.join(process.env.HOME || '', '.local', 'state')
const OMARCHY_CURRENT = path.join(STATE_HOME, 'omarchy', 'current')
const APP_DATA_DIR = path.join(
  process.env.XDG_DATA_HOME || path.join(process.env.HOME || '', '.local', 'share'),
  'agent-board',
  'electron'
)

fs.mkdirSync(APP_DATA_DIR, { recursive: true })
app.setName('Agent Board')
app.setPath('userData', APP_DATA_DIR)
// This is a transparent always-on-top overlay, not a game renderer. Software
// rendering avoids Chromium GPU-process conflicts with T3 Code and other
// Electron apps while keeping the interface responsive.
app.disableHardwareAcceleration()

let omarchyTheme = loadOmarchyTheme()
let themeWatchTimer = null
let themePollTimer = null

function applyOmarchyTheme(theme) {
  nativeTheme.themeSource = theme.appearance === 'light' ? 'light' : 'dark'
  if (win && !win.isDestroyed()) {
    win.setBackgroundColor('#00000000')
    win.webContents.send('wow:theme', theme)
  }
}

function refreshOmarchyTheme() {
  const next = loadOmarchyTheme()
  if (themeSignature(next) === themeSignature(omarchyTheme)) return
  omarchyTheme = next
  applyOmarchyTheme(next)
}

function startThemeWatcher() {
  const schedule = () => {
    clearTimeout(themeWatchTimer)
    themeWatchTimer = setTimeout(refreshOmarchyTheme, 100)
  }
  try {
    fs.watch(OMARCHY_CURRENT, { persistent: false }, schedule)
  } catch {
    // Polling below still catches atomic theme swaps.
  }
  themePollTimer = setInterval(refreshOmarchyTheme, 1500)
}

const GEOMETRY = {
  badge: { width: 150, height: 26 },
  board: { width: 980, height: 720 }
}
const MARGIN = 24

let win = null
let mode = START_MODE
let rosterTimer = null
let selfTestTimer = null
let latestRoster = null
let selectedThreadId = null
let nextFallbackAt = 0
let lastLiveRevision = -1
let refreshingLive = false
let hiddenForGame = false
let gameTimer = null
let modeEpoch = 0
let modeTransition = Promise.resolve()
const modeFrames = new Map()
const POSITION_PATH = path.join(APP_DATA_DIR, 'window-positions.json')
let positions = {}
try { positions = JSON.parse(fs.readFileSync(POSITION_PATH, 'utf8')) } catch {}
let displayedMode = null
let transitioning = false
let samplingPosition = false

// The board keeps the size you give it; the badge is always its own size.
const MIN_BOARD = { width: 640, height: 460 }

function geometryFor(shape) {
  const base = GEOMETRY[shape] || GEOMETRY.board
  const saved = positions[shape]
  if (shape !== 'board' || !Number.isFinite(saved?.width) || !Number.isFinite(saved?.height)) return base
  return { width: Math.max(MIN_BOARD.width, saved.width), height: Math.max(MIN_BOARD.height, saved.height) }
}

function rememberPosition(shape, position) {
  if (!shape || !Number.isFinite(position?.x) || !Number.isFinite(position?.y)) return
  const sized = shape === 'board' && Number.isFinite(position.width) && Number.isFinite(position.height)
  const previous = positions[shape]
  if (previous?.x === position.x && previous?.y === position.y &&
    (!sized || (previous.width === position.width && previous.height === position.height))) return
  positions[shape] = { x: Math.round(position.x), y: Math.round(position.y),
    ...(sized ? { width: Math.round(position.width), height: Math.round(position.height) } : {}) }
  try {
    fs.writeFileSync(`${POSITION_PATH}.tmp`, JSON.stringify(positions), { mode: 0o600 })
    fs.renameSync(`${POSITION_PATH}.tmp`, POSITION_PATH)
  } catch (error) { log(`Could not save window position: ${error.message}`) }
}

async function nativePosition() {
  if (!win || win.isDestroyed() || HEADLESS || hiddenForGame) return null
  if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
    return new Promise(resolve => {
      execFile('hyprctl', ['clients', '-j'], { timeout: 1500 }, (error, stdout) => {
        if (error) return resolve(null)
        try {
          const client = JSON.parse(stdout).find(item => item.pid === process.pid)
          resolve(client?.at ? { x: client.at[0], y: client.at[1], width: client.size?.[0], height: client.size?.[1] } : null)
        } catch { resolve(null) }
      })
    })
  }
  return win.getBounds()
}

async function samplePosition() {
  if (samplingPosition || transitioning || !displayedMode) return
  samplingPosition = true
  const epoch = modeEpoch
  try {
    const position = await nativePosition()
    if (!transitioning && epoch === modeEpoch) rememberPosition(displayedMode, position)
  } finally { samplingPosition = false }
}

const log = message => {
  try {
    fs.appendFileSync(LOG_PATH, `${new Date().toISOString()} ${message}\n`)
  } catch {
    // Logging must never be the thing that breaks the overlay.
  }
}

function cli(args, timeout = 20000) {
  return new Promise(resolve => {
    execFile(CLI, args, { timeout, env: process.env }, (error, stdout, stderr) =>
      resolve({ error, stdout: stdout || '', stderr: stderr || '' })
    )
  })
}

function send(channel, payload) {
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, payload)
  }
}

function gameRunning() {
  const wanted = new Set(['wow.exe', 'wowclassic.exe', 'wowb.exe', 'wowt.exe', 'wowclassic', 'wowb', 'wowt'])
  try {
    for (const entry of fs.readdirSync('/proc')) {
      if (!/^\d+$/.test(entry)) continue
      let raw = ''
      try {
        raw = fs.readFileSync(`/proc/${entry}/cmdline`, 'utf8')
      } catch {
        continue
      }
      const arguments_ = raw.split('\0').filter(Boolean)
      if (!arguments_.length) continue
      const executable = arguments_[0].replaceAll('\\', '/').split('/').pop().toLowerCase()
      if (wanted.has(executable)) return true
      if (executable.includes('wine')) {
        for (const argument of arguments_.slice(1)) {
          const name = argument.replaceAll('\\', '/').split('/').pop().toLowerCase()
          if (wanted.has(name)) return true
        }
      }
    }
  } catch {
    return false
  }
  return false
}

function syncGameVisibility() {
  if (HEADLESS || !GAME_AWARE || !win || win.isDestroyed()) return
  if (gameRunning()) {
    if (hiddenForGame) {
      hiddenForGame = false
      void applyMode(mode)
    }
  } else {
    hiddenForGame = true
    win.setOpacity(0)
    win.setIgnoreMouseEvents(true)
    win.hide()
  }
}

async function pinWindow(nextMode, returnFocus = false) {
  if (!PIN_ENABLED) {
    return
  }

  const geometry = geometryFor(nextMode)
  const anchor = process.env.AGENT_BOARD_ANCHOR || 'game'
  const { error, stdout } = await cli([
    'pin',
    '--mode',
    nextMode,
    '--anchor',
    anchor,
    '--margin',
    String(MARGIN),
    '--width',
    String(geometry.width),
    '--height',
    String(geometry.height),
    ...(returnFocus ? ['--return-focus'] : []),
    ...((positions[nextMode] || positions.badge) ? ['--x', String((positions[nextMode] || positions.badge).x), '--y', String((positions[nextMode] || positions.badge).y)] : [])
  ])

  if (error) {
    log(`pin(${nextMode}) failed: ${(stdout || error.message).trim()}`)
  }
}

function placeInitial() {
  if (!win) {
    return
  }

  const area = screen.getPrimaryDisplay().workArea
  const geometry = geometryFor(mode)
  win.setBounds({
    x: Math.max(area.x, area.x + area.width - geometry.width - MARGIN),
    y: area.y + MARGIN,
    width: geometry.width,
    height: geometry.height
  })
}

function applyMode(nextMode) {
  if (!Object.hasOwn(GEOMETRY, nextMode)) return
  mode = nextMode
  const epoch = ++modeEpoch
  // Serialize compositor requests as well as DOM updates. A slow board pin must
  // never finish after a later collapse and leave a board-sized badge surface.
  modeTransition = modeTransition.catch(error => log(error.message)).then(async () => {
    if (!win || win.isDestroyed() || epoch !== modeEpoch) return
    transitioning = true
    if (displayedMode) rememberPosition(displayedMode, await nativePosition())
    displayedMode = null
    if (epoch !== modeEpoch) return
    const geometry = geometryFor(nextMode)
    const returnFocus = nextMode === 'badge' && win.isFocused()
    win.setFocusable(nextMode === 'board')
    if (HEADLESS) {
      win.setSize(geometry.width, geometry.height)
      send('wow:mode', { mode: nextMode })
      return
    }
    if (GAME_AWARE && !gameRunning()) {
      hiddenForGame = true
      win.setOpacity(0)
      win.setIgnoreMouseEvents(true)
      win.hide()
      send('wow:mode', { mode: nextMode })
      return
    }
    win.setOpacity(0)
    win.setIgnoreMouseEvents(true)
    win.setSize(geometry.width, geometry.height)
    win.showInactive()
    // Native Wayland may ignore Electron's requested size. The compositor is
    // authoritative for both placement and dimensions on Hyprland.
    await pinWindow(nextMode, returnFocus)
    if (!win || win.isDestroyed() || epoch !== modeEpoch) return
    hiddenForGame = false
    await new Promise(resolve => {
      const timer = setTimeout(() => { modeFrames.delete(epoch); resolve() }, 1000)
      modeFrames.set(epoch, () => { clearTimeout(timer); resolve() })
      send('wow:mode', { mode: nextMode, epoch })
    })
    if (!win || win.isDestroyed() || epoch !== modeEpoch) return
    win.setOpacity(1)
    win.setIgnoreMouseEvents(false)
    if (nextMode === 'board') win.focus()
    displayedMode = nextMode
    transitioning = false
    await samplePosition()
  })
  return modeTransition
}

async function refreshRoster() {
  const { error, stdout } = await cli(['board', '--json', '--compact', '--limit', '15', '--days', '3'])

  if (error || !stdout.trim()) {
    return
  }

  let payload
  try {
    payload = JSON.parse(stdout.trim().split('\n').pop())
  } catch {
    log('roster payload was not JSON')
    return
  }

  latestRoster = payload
  send('wow:roster', payload)

  if (SELF_TEST && !selfTestTimer) {
    selfTestTimer = setTimeout(runSelfTest, 900)
  }
}

let focusedSession = ''
let liveWait = null

// `agent-board demo` leaves a marker naming its socket and pid. While that
// process lives, the overlay reads the demo world instead of the real bridge,
// so a recording never shows anyone's real sessions.
const DEMO_MARKER = path.join(RUNTIME_DIR, 'agent-board-demo.json')
let liveSource = null

function demoSocket() {
  try {
    const marker = JSON.parse(fs.readFileSync(DEMO_MARKER, 'utf8'))
    process.kill(Number(marker.pid), 0)
    return typeof marker.socket === 'string' && marker.socket ? marker.socket : null
  } catch {
    return null
  }
}

function currentLiveSocket() {
  const demo = demoSocket()
  const next = demo || LIVE_SOCKET_PATH
  if (next !== liveSource) {
    const first = liveSource === null
    liveSource = next
    lastLiveRevision = -1
    latestRoster = null
    // Whatever was on screen belongs to the other source: drop it.
    if (!first) send('wow:reset', { demo: Boolean(demo) })
  }
  return next
}

function liveRequest(payload, timeout = 8000) {
  return new Promise(resolve => {
    let buffer = ''
    let settled = false
    const socket = net.connect(currentLiveSocket())
    const done = result => {
      if (settled) return
      settled = true
      if (liveWait?.socket === socket) liveWait = null
      socket.destroy()
      resolve(result)
    }
    if (payload.wait) liveWait = { socket, done }

    socket.setTimeout(timeout)
    socket.on('connect', () => socket.write(`${JSON.stringify(payload)}\n`))
    socket.on('data', chunk => {
      buffer += chunk.toString()
      const newline = buffer.indexOf('\n')
      if (newline < 0) return
      try {
        done(JSON.parse(buffer.slice(0, newline)))
      } catch (error) {
        done({ ok: false, error: `live bridge returned bad JSON: ${error.message}` })
      }
    })
    socket.on('end', () => done({ ok: false, error: 'Live bridge disconnected before confirming delivery.' }))
    socket.on('error', error => done({ ok: false, error: error.message }))
    socket.on('timeout', () => done({ ok: false, error: 'live bridge timed out' }))
  })
}

async function refreshLive(wait = 0) {
  if (refreshingLive) return false
  refreshingLive = true
  try { return await fetchLive(wait) } finally { refreshingLive = false }
}

async function fetchLive(wait = 0) {
  // With a known revision the bridge holds the request until the board changes
  // (or the wait runs out), so updates arrive as they happen, not on a timer.
  const waiting = wait > 0 && lastLiveRevision >= 0
  const response = await liveRequest(
    { type: 'state', revision: lastLiveRevision, conversation_for: focusedSession, ...(waiting ? { wait } : {}) },
    waiting ? (wait + 5) * 1000 : 8000
  )
  if (response.aborted) return true
  if (response.ok && response.unchanged) {
    send('wow:live', { connected: true, revision: response.revision })
    return true
  }
  if (response.ok && response.board) {
    if (latestRoster === null || response.revision !== lastLiveRevision) {
      latestRoster = response.board
      send('wow:roster', latestRoster)
      lastLiveRevision = response.revision
    }
    send('wow:live', { connected: true, revision: response.revision })
    if (SELF_TEST && !selfTestTimer) {
      selfTestTimer = setTimeout(runSelfTest, 900)
    }
    return true
  }

  lastLiveRevision = -1
  send('wow:live', { connected: false, error: response.error || 'live bridge unavailable' })
  // Manual source overlays can still use the old snapshot path, but never at the
  // live polling rate: that would repeatedly spawn provider processes. Never in
  // a demo, where the fallback would put real sessions on screen.
  if (!demoSocket() && Date.now() >= nextFallbackAt) {
    nextFallbackAt = Date.now() + REFRESH_MS
    await refreshRoster()
  }
  return false
}

async function liveAction(action) {
  const response = await liveRequest({ type: 'action', action }, 40000)
  if (!response.ok) {
    return { ok: false, error: response.error || response.message || 'action failed' }
  }
  void refreshLive()
  return response
}

const LIVE_WAIT_S = 20

function startRosterLoop() {
  let stopped = false
  rosterTimer = { stop: () => { stopped = true } }
  void (async () => {
    while (!stopped) {
      const started = Date.now()
      const before = lastLiveRevision
      const ok = await refreshLive(LIVE_WAIT_S)
      // A failed request, or a bridge too old to hold the request open, answers
      // at once with nothing new; fall back to the poll interval instead of spinning.
      const quickAndUnchanged = lastLiveRevision === before && lastLiveRevision >= 0 && Date.now() - started < 100
      if (!ok || quickAndUnchanged) await new Promise(resolve => setTimeout(resolve, LIVE_POLL_MS))
    }
  })()
}

function runSelfTest() {
  if (!win || win.isDestroyed()) {
    app.exit(1)
    return
  }

  win.webContents
    .executeJavaScript('document.body.innerText', true)
    .then(async text => {
      if (SELF_TEST_SCREENSHOT) {
        const image = await win.webContents.capturePage()
        fs.writeFileSync(SELF_TEST_SCREENSHOT, image.toPNG())
      }
      process.stdout.write(`SELFTEST-OK\n${text}\n`)
      app.exit(0)
    })
    .catch(error => {
      process.stdout.write(`SELFTEST-FAIL ${error.message}\n`)
      app.exit(1)
    })
}

async function handleCommand(payload) {
  const command = String(payload.cmd || '')

  if (command.startsWith('focus:')) {
    const sessionId = command.slice('focus:'.length).trim()
    if (!sessionId) return { ok: false, error: 'missing session id' }
    selectedThreadId = sessionId
    applyMode('board')
    send('wow:focus', { sessionId })
    return { ok: true, mode, sessionId }
  }

  // Demo autoplay drives the real UI: press a button, type into the composer.
  // Only while a demo is running, so nothing can type into real sessions.
  if (command.startsWith('demo:')) {
    if (!demoSocket()) return { ok: false, error: 'no demo is running' }
    const [, verb, ...rest] = command.split(':')
    const value = rest.join(':')
    if (verb === 'click') send('wow:demo', { click: value })
    else if (verb === 'type') send('wow:demo', { type: value })
    else return { ok: false, error: `unknown demo command: ${verb}` }
    return { ok: true }
  }

  if (command.startsWith('mode:')) {
    const requested = command.slice('mode:'.length)
    applyMode(requested === 'toggle' ? (mode === 'board' ? 'badge' : 'board') : requested)
    return { ok: true, mode }
  }

  switch (command) {
    case 'ping':
      return { ok: true, mode, game_aware: GAME_AWARE, theme: omarchyTheme.name, bounds: win?.getBounds() }
    case 'toggle':
      applyMode(mode === 'board' ? 'badge' : 'board')
      return { ok: true, mode }
    case 'badge':
      applyMode('badge')
      return { ok: true, mode }
    case 'board':
      applyMode('board')
      return { ok: true, mode }
    case 'show':
      if (win) win.show()
      applyMode('board')
      return { ok: true, mode }
    case 'hide':
      if (win) win.hide()
      return { ok: true, hidden: true }
    case 'refresh':
      await refreshLive()
      return { ok: true }
    case 'live-reset':
      lastLiveRevision = -1
      liveWait?.done({ ok: false, aborted: true })
      currentLiveSocket()
      return { ok: true, demo: Boolean(demoSocket()) }
    case 'quit':
      await samplePosition()
      setTimeout(() => {
        app.quit()
        // A transparent pinned toplevel on Wayland can outlive a polite quit;
        // the socket owner has to actually go away or the next launch sees a
        // ghost and refuses to start.
        setTimeout(() => app.exit(0), 1500)
      }, 50)
      return { ok: true, quitting: true }
    default:
      return { ok: false, error: `unknown command: ${command}` }
  }
}

function startControlServer() {
  try {
    fs.unlinkSync(SOCKET_PATH)
  } catch {
    // no stale socket
  }

  const server = net.createServer(socket => {
    let buffer = ''

    socket.on('data', chunk => {
      buffer += chunk.toString()

      let index
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)

        if (!line) {
          continue
        }

        let payload
        try {
          payload = JSON.parse(line)
        } catch {
          socket.write(`${JSON.stringify({ ok: false, error: 'bad json' })}\n`)
          continue
        }

        if (payload.session_id && payload.text) {
          void reply(payload.session_id, payload.text).then(result => {
            try {
              socket.write(`${JSON.stringify(result)}\n`)
            } catch {
              // peer went away
            }
          })
          continue
        }

        void handleCommand(payload).then(result => {
          try {
            socket.write(`${JSON.stringify(result)}\n`)
          } catch {
            // peer went away
          }
        })
      }
    })

    socket.on('error', () => {})
  })

  server.on('error', error => log(`control socket: ${error.message}`))
  server.listen(SOCKET_PATH, () => fs.chmodSync(SOCKET_PATH, 0o600))
}

async function reply(sessionId, text) {
  const session = (latestRoster?.sessions || []).find(item => item.id === sessionId)
  const result = await liveAction({
    kind: 'reply',
    provider: session?.provider || 't3',
    host: session?.host || 'local',
    session_id: String(sessionId),
    text: String(text)
  })
  if (!result.ok) log(`reply failed: ${result.error || result.message || 'unknown error'}`)
  return result
}

async function alreadyRunning() {
  return new Promise(resolve => {
    const probe = net.connect(SOCKET_PATH)
    const done = value => {
      probe.destroy()
      resolve(value)
    }
    probe.once('connect', () => done(true))
    probe.once('error', () => done(false))
    setTimeout(() => done(false), 400)
  })
}

function createWindow() {
  const geometry = geometryFor(mode)

  win = new BrowserWindow({
    width: geometry.width,
    height: geometry.height,
    frame: false,
    transparent: true,
    resizable: true,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    hasShadow: false,
    backgroundColor: '#00000000',
    acceptFirstMouse: true,
    focusable: mode === 'board',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false
    }
  })

  win.setAlwaysOnTop(true, 'screen-saver')
  win.setIgnoreMouseEvents(false)
  placeInitial()

  win.loadFile(path.join(__dirname, 'index.html'))

  // The first poll usually wins the race against the renderer's own load, so
  // whatever we already know is replayed the moment the page can hear it.
  win.webContents.on('did-finish-load', () => {
    win.webContents.send('wow:mode', { mode })
    win.webContents.send('wow:theme', omarchyTheme)
    if (latestRoster) {
      win.webContents.send('wow:roster', latestRoster)
      win.webContents.send('wow:live', { connected: true })
    }
  })

  // On Wayland a hidden transparent window never paints, so ready-to-show may
  // never fire and the badge would stay unmapped until a mode change. The page
  // load is the fallback; whichever arrives first shows the window once.
  let firstShown = false
  const showFirst = () => {
    if (firstShown || !win || win.isDestroyed()) return
    firstShown = true
    if (HEADLESS) {
      // A self test renders the real markup without ever mapping a window over
      // whatever the user is doing.
      win.webContents.send('wow:mode', { mode })
      return
    }

    syncGameVisibility()
    if (!hiddenForGame) {
      void applyMode(mode)
    }
  }
  win.once('ready-to-show', showFirst)
  win.webContents.once('did-finish-load', showFirst)

  win.on('closed', () => {
    win = null
  })

  win.webContents.on('render-process-gone', (_event, details) => {
    log(`renderer gone: ${details.reason}`)
    if (!SELF_TEST) setTimeout(() => win?.reload(), 400)
  })
  win.on('unresponsive', () => {
    log('window unresponsive; reloading renderer')
    if (!SELF_TEST) win?.webContents.reload()
  })
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
}

app.on('child-process-gone', (_event, details) => {
  log(`Electron child process gone: ${details.type}/${details.reason}`)
})

app.on('before-quit', () => {
  clearInterval(themePollTimer)
  clearTimeout(themeWatchTimer)
})

ipcMain.on('wow:mode-ready', (_event, epoch) => {
  const ready = modeFrames.get(epoch)
  modeFrames.delete(epoch)
  ready?.()
})

ipcMain.on('wow:reply', (_event, payload) => {
  void reply(payload?.sessionId, payload?.text)
})

ipcMain.handle('wow:action', (_event, action) => liveAction(action || {}))

ipcMain.handle('wow:choose-directory', async () => {
  const result = await dialog.showOpenDialog(win, {
    title: 'Add a T3 Code project',
    defaultPath: process.env.HOME,
    properties: ['openDirectory', 'createDirectory'],
  })
  return result.canceled || !result.filePaths.length
    ? { ok: false, cancelled: true }
    : { ok: true, path: result.filePaths[0] }
})

ipcMain.on('wow:badge-width', (_event, width) => {
  const next = Math.max(96, Math.min(280, Math.round(Number(width) || 0)))
  if (!next || next === GEOMETRY.badge.width) return
  GEOMETRY.badge.width = next
  // Re-pin at the same spot once any transition in flight has settled: on
  // Wayland the compositor owns the size.
  void modeTransition.then(() => {
    if (mode === 'badge' && !hiddenForGame) return applyMode('badge')
  })
})

ipcMain.on('wow:open-external', (_event, url) => {
  // Links in agent replies open in the browser; anything but http(s) is ignored.
  try {
    const parsed = new URL(String(url || ''))
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') void shell.openExternal(parsed.href)
  } catch {}
})

ipcMain.on('wow:select', (_event, sessionId) => {
  const next = String(sessionId || '')
  if (next === focusedSession) return
  focusedSession = next
  // The board in hand carries the previous session's conversation: fetch again
  // now instead of waiting out the current long poll.
  lastLiveRevision = -1
  liveWait?.done({ ok: false, aborted: true })
})

ipcMain.on('wow:command', (_event, command) => {
  void handleCommand({ cmd: command })
})

void (async () => {
  await app.whenReady()

  // Two overlays would fight over the same socket and paint two bars over the
  // game. Electron's own lock is the authority; the socket probe stays as the
  // belt to that braces.
  if (!SELF_TEST && !app.requestSingleInstanceLock()) {
    log('another overlay owns the Electron lock; exiting')
    app.exit(0)
    return
  }

  if (!SELF_TEST && (await alreadyRunning())) {
    log('another overlay already owns the control socket; exiting')
    app.exit(0)
    return
  }

  createWindow()
  startControlServer()
  startRosterLoop()
  startThemeWatcher()
  setInterval(() => void samplePosition(), 500)
  applyOmarchyTheme(omarchyTheme)
  if (GAME_AWARE) gameTimer = setInterval(syncGameVisibility, 3000)
})()

process.on('SIGTERM', () => app.exit(0))
process.on('SIGINT', () => app.exit(0))
app.on('window-all-closed', () => app.quit())
