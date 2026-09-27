'use strict'

// The renderer owns presentation and keyboard state only. Live state and actions
// travel through the preload bridge to the long-running Agent Board service.

const body = document.body
const badge = document.getElementById('badge')
const badgeText = badge.querySelector('.badge-text')
const rowsEl = document.getElementById('rows')
const emptyEl = document.getElementById('empty')
const liveStateEl = document.getElementById('live-state')
const searchInput = document.getElementById('search-input')
const welcomeEl = document.getElementById('welcome')
const detailEl = document.getElementById('session-detail')
const detailProvider = document.getElementById('detail-provider')
const detailTitle = document.getElementById('detail-title')
const detailMeta = document.getElementById('detail-meta')
const detailStatus = document.getElementById('detail-status')
const questionsEl = document.getElementById('questions')
let questionsKey = ''
let conversationKey = ''
const attentionEl = document.getElementById('attention')
const conversationEl = document.getElementById('conversation')
const approveButton = document.getElementById('approve-button')
const declineButton = document.getElementById('decline-button')
const answerButton = document.getElementById('answer-button')
const markReadButton = document.getElementById('mark-read-button')
const stopButton = document.getElementById('stop-button')
const actionStatus = document.getElementById('action-status')
const composerInput = document.getElementById('composer-input')
const queueEl = document.getElementById('message-queue')
const sendNowButton = document.getElementById('send-now-button')
const sendButton = document.getElementById('send-button')
const boardStatus = document.getElementById('board-status')
const sessionWindow = document.getElementById('session-window')
const newSessionModal = document.getElementById('new-session')
const projectDropdown = document.getElementById('project-dropdown')
const projectTrigger = document.getElementById('project-trigger')
const projectTriggerLabel = document.getElementById('project-trigger-label')
const projectMenu = document.getElementById('project-menu')
const newSessionPrompt = document.getElementById('new-session-prompt')
const newSessionStatus = document.getElementById('new-session-status')
const newSessionStart = document.getElementById('new-session-start')
const addProjectButton = document.getElementById('add-project-button')
const selectedProjectPath = document.getElementById('selected-project-path')

const STATUS_ORDER = ['needs', 'error', 'working', 'waiting', 'reply', 'idle', 'finished']
let board = { sessions: [], counts: {}, projects: [] }
let selectedId = null
let selectedCache = null
let search = ''
let mode = 'badge'
let connected = false
let busy = false
let startingSession = false
let addingProject = false
let queueKey = ''
let actionStatusTimer = null
let modalReturnFocus = null
let scrollFrame = null
let lastDetailId = null
let statusTimer = null
let selectedProjectId = null
const provisionalProjects = new Map()
const pendingMessages = new Map()
let savedDrafts = {}
try {
  const stored = JSON.parse(localStorage.getItem('composer-drafts-v1') || '{}')
  if (stored && typeof stored === 'object' && !Array.isArray(stored)) savedDrafts = stored
} catch {}
const drafts = new Map(Object.entries(savedDrafts))
function saveDraft() {
  if (!selectedId) return
  drafts.set(selectedId, composerInput.value)
  try { localStorage.setItem('composer-drafts-v1', JSON.stringify(Object.fromEntries(drafts))) }
  catch { setStatus('Draft storage is full. Keep this window open until your message is sent.', true) }
}
function fitComposer() {
  composerInput.style.height = 'auto'
  composerInput.style.height = `${Math.min(composerInput.scrollHeight + 2, window.innerHeight <= 540 ? 90 : 180)}px`
}
composerInput.addEventListener('input', () => { saveDraft(); fitComposer(); updateControls() })
window.addEventListener('beforeunload', saveDraft)
const queue = new window.MessageQueue({
  storage: localStorage,
  send: action => window.wow.action(action),
  changed: () => queueMicrotask(() => { window.wow.queueSessions?.(queue.items.map(item => item.action.session_id), queue.items.reduce((counts, item) => { counts[item.state] = (counts[item.state] || 0) + 1; return counts }, {})); renderBadge(); if (selectedSession()) renderQueue(selectedSession()) })
})

function renderQueue(session) {
  const items = queue.list(session)
  queueEl.hidden = items.length === 0
  const active = queue.active(session) || items.length > 0 || !providerAvailable(session)
  sendButton.textContent = session.user_input_request_id ? 'Answer' : active ? 'Queue message' : 'Send'
  sendNowButton.hidden = !active || Boolean(session.user_input_request_id)
  updateControls()
  const key = JSON.stringify([sessionKey(session), items, busy, providerAvailable(session), session.approval_request_id, session.user_input_request_id, queue.inflight.has(queue.key(session))])
  if (key === queueKey) return
  queueKey = key
  queueEl.replaceChildren()
  for (const item of items) {
    const row = document.createElement('div')
    row.className = 'queued-message'
    const text = document.createElement('div')
    text.className = 'queued-text'
    const waiting = !providerAvailable(session) ? 'Queued while offline' : session.approval_request_id || session.user_input_request_id ? 'Queued until you answer' : 'Queued for next turn'
    const label = item.state === 'queued' ? waiting : item.state === 'held' ? 'Needs review' : item.state === 'sending' ? 'Sending…' : item.receipt || 'Sent'
    text.textContent = `${label}: ${item.text}`
    row.append(text)
    if (item.error) {
      const error = document.createElement('div')
      error.className = 'queue-error'
      error.textContent = item.error
      row.append(error)
    }
    if (['queued', 'held'].includes(item.state)) {
      const now = document.createElement('button')
      now.className = 'button quiet'
      now.textContent = item.state === 'held' ? 'Retry now' : 'Send now'
      now.disabled = busy || !providerAvailable(session) || Boolean(session.approval_request_id || session.user_input_request_id) || queue.inflight.has(queue.key(session))
      now.addEventListener('click', () => void queue.sendNow(item.id, session))
      const edit = document.createElement('button')
      edit.className = 'button quiet'
      edit.textContent = 'Edit'
      edit.addEventListener('click', () => {
        const removed = queue.remove(item.id)
        if (removed) { composerInput.value = [composerInput.value, removed.text].filter(Boolean).join('\n\n'); saveDraft(); fitComposer(); updateControls() }
        composerInput.focus()
      })
      const remove = document.createElement('button')
      remove.className = 'button quiet'
      remove.textContent = 'Remove'
      remove.addEventListener('click', () => queue.remove(item.id))
      row.append(now, edit, remove)
    }
    queueEl.append(row)
  }
}

function age(seconds) {
  const value = Number(seconds || 0)
  if (value < 60) return 'now'
  if (value < 3600) return `${Math.floor(value / 60)}m`
  if (value < 86400) return `${Math.floor(value / 3600)}h`
  return `${Math.floor(value / 86400)}d`
}

function providerLabel(session) {
  return session.provider_label || (session.provider === 't3' ? 'T3 Code' : 'Hermes')
}

function sessionMatchesSearch(session) {
  if (!search) return true
  const haystack = [
    session.title,
    session.project,
    session.source,
    session.activity,
    session.id,
    providerLabel(session)
  ].join(' ').toLowerCase()
  return haystack.includes(search)
}

// The list is grouped by what the session wants from you, not by provider:
// only a real request blocks on you; then live work; then what just ended and
// you have not read; then everything else, including news that has gone stale.
const GROUPS = [
  { key: 'needs', label: 'Needs you' },
  { key: 'running', label: 'Running' },
  { key: 'recent', label: 'Just finished' },
  { key: 'earlier', label: 'Earlier' }
]
// How long an ending counts as "just finished": unread output for three
// hours, anything at all for half an hour. Past that it is something you have
// probably seen elsewhere, and it steps back.
const RECENT_SECONDS = 3 * 3600
const JUST_ENDED_SECONDS = 30 * 60

// Older bridges do not send `unread`; treat that as unread so nothing hides.
function isUnread(session) {
  return session.unread !== false
}

// Unread output: a reply, a failure, or a question in the last message.
function hasNews(session) {
  if (['reply', 'error', 'needs'].includes(session.status)) return isUnread(session)
  return session.status === 'finished' && session.unread === true
}

function sessionGroup(session) {
  if (session.approval_request_id || session.user_input_request_id) return 'needs'
  if (['working', 'waiting', 'starting'].includes(session.status)) return 'running'
  const age = Number(session.age_s || 0)
  if (hasNews(session) && age <= RECENT_SECONDS) return 'recent'
  if (['reply', 'error', 'needs', 'finished'].includes(session.status) && age <= JUST_ENDED_SECONDS) return 'recent'
  return 'earlier'
}

function rowStatus(session) {
  if (session.approval_request_id) return 'Needs approval'
  if (session.user_input_request_id) return 'Needs an answer'
  if (session.status === 'error') return 'Failed'
  const labels = { needs: 'Asked a question', working: 'Running', waiting: 'Starting', starting: 'Starting', reply: 'Unread reply', finished: 'Finished', idle: 'Idle' }
  if (session.status === 'reply' && !isUnread(session)) return 'Finished'
  return labels[session.status] || session.status_label || session.status || ''
}

// Opening a session with news in it counts as reading it, as in any chat app.
let autoReadTimer = null
function scheduleAutoRead(session) {
  clearTimeout(autoReadTimer)
  if (!session || mode !== 'board' || !providerAvailable(session)) return
  if (!hasNews(session) || !(session.capabilities || []).includes('mark_read')) return
  autoReadTimer = setTimeout(() => {
    const current = selectedSession()
    if (!current || sessionKey(current) !== sessionKey(session) || mode !== 'board' || document.hidden || !providerAvailable(session)) return
    if (conversationEl.scrollHeight - conversationEl.clientHeight - conversationEl.scrollTop >= 60) return
    void safeAction({ kind: 'mark_read', provider: session.provider, host: session.host || 'local', session_id: session.id, text: String(session.activity_at || '') })
  }, 1500)
}

function visibleSessions() {
  const order = Object.fromEntries(GROUPS.map((group, index) => [group.key, index]))
  return [...(board.sessions || [])]
    .filter(sessionMatchesSearch)
    .sort((left, right) => (order[sessionGroup(left)] - order[sessionGroup(right)]) ||
      Number(right.activity_at || 0) - Number(left.activity_at || 0))
}

function sessionKey(session) {
  if (!session) return null
  return session.host && session.host !== 'local' ? JSON.stringify([session.host, session.provider, session.id]) : session.id
}

function selectedSession() {
  return (board.sessions || []).find(session => sessionKey(session) === selectedId) || selectedCache
}

let lastBadgeScore = null
let reportedBadgeWidth = 0

// The badge window is as wide as its words: "Idle" should not sit in a bar
// built for "1 needs you · 2 running".
function fitBadge() {
  if (mode !== 'badge') return
  // Sum the parts rather than subtract from the window: right after a mode
  // switch the window can still be board-sized.
  const style = getComputedStyle(badge)
  const parts = [...badge.children].filter(child => child !== badgeText)
    .reduce((total, child) => total + child.getBoundingClientRect().width + (parseFloat(getComputedStyle(child).marginLeft) || 0), 0)
  const gaps = (parseFloat(style.columnGap) || 0) * (badge.children.length - 1)
  const chrome = parts + gaps + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) +
    parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth)
  const width = Math.max(96, Math.min(280, Math.ceil(badgeText.scrollWidth + chrome + 2)))
  if (Math.abs(width - reportedBadgeWidth) < 3) return
  reportedBadgeWidth = width
  window.wow.badgeWidth?.(width)
}

function renderBadge() {
  const queued = queue.items.filter(item => ['queued', 'held'].includes(item.state)).length
  const state = window.badgeState(board, connected, queued)
  badgeText.textContent = state.label
  badge.classList.toggle('attention', state.attention)
  badge.classList.toggle('running', Boolean(state.running))
  // A new request or a new reply earns a short pulse, not a permanent alarm.
  const score = (state.requests || 0) * 1000 + (state.fresh || 0)
  if (lastBadgeScore !== null && score > lastBadgeScore) {
    badge.classList.remove('ping')
    void badge.offsetWidth
    badge.classList.add('ping')
  }
  lastBadgeScore = score
  requestAnimationFrame(fitBadge)
  badgeText.classList.toggle('hot', state.attention)
  badge.title = `${state.label}\n${state.detail}\nSuper+Alt+C to toggle`
}

let rowsKey = ''

function renderRows() {
  const sessions = visibleSessions()
  // A roster update arrives several times a second while agents run; rebuilding
  // identical rows would reset hover and focus for nothing.
  const key = JSON.stringify([search, selectedId, sessions.map(session => [sessionKey(session), session.host_offline, session.status, session.title, session.activity,
    session.age_s < 60 ? 0 : Math.floor(session.age_s / 60), session.approval_request_id, session.user_input_request_id, session.project,
    session.unread, session.snippet, session.source, session.provider_label, session.status_label, sessionGroup(session)])])
  if (key === rowsKey && (selectedId || !sessions.length)) return
  rowsKey = key
  const focusedId = document.activeElement?.closest?.('.row')?.dataset.id
  const scroll = rowsEl.scrollTop
  rowsEl.replaceChildren()
  emptyEl.hidden = sessions.length > 0
  emptyEl.textContent = search ? 'No sessions match your search.' : 'No active or recent sessions.'

  if (!selectedId && sessions.length) {
    selectedId = sessionKey(sessions[0])
    selectedCache = sessions[0]
    composerInput.value = drafts.get(selectedId) || ''
  }

  let currentGroup = null
  for (const session of sessions) {
    const group = sessionGroup(session)
    if (group !== currentGroup) {
      currentGroup = group
      const heading = document.createElement('div')
      heading.className = `group-heading ${group}`
      const label = document.createElement('span')
      label.textContent = GROUPS.find(item => item.key === group).label
      const count = document.createElement('span')
      count.className = 'group-count'
      count.textContent = String(sessions.filter(item => sessionGroup(item) === group).length)
      heading.append(label, count)
      rowsEl.append(heading)
    }
    const row = document.createElement('div')
    row.dataset.id = sessionKey(session)
    row.tabIndex = sessionKey(session) === selectedId || !sessions.some(item => sessionKey(item) === selectedId) && session === sessions[0] ? 0 : -1
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSession(sessionKey(session), true) }
      if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
        event.preventDefault()
        const box = row.getBoundingClientRect()
        openRowMenu(sessionKey(session), box.left + 24, box.bottom - 6)
      }
    })
    row.className = `row ${group}${sessionKey(session) === selectedId ? ' selected' : ''}`
    row.setAttribute('role', 'option')
    row.setAttribute('aria-selected', sessionKey(session) === selectedId ? 'true' : 'false')

    const dot = document.createElement('i')
    dot.className = `dot ${session.status}`

    const main = document.createElement('div')
    main.className = 'row-main'
    const title = document.createElement('div')
    title.className = 'row-title'
    title.textContent = session.title || '(untitled)'
    const status = document.createElement('div')
    status.className = `row-status ${session.status}`
    const statusWord = document.createElement('span')
    statusWord.className = 'row-status-word'
    statusWord.textContent = rowStatus(session)
    status.append(statusWord)
    // The status word already says what kind of state this is; the activity
    // text only adds something when it says more than the label.
    const detail = group === 'earlier' ? ''
      : group === 'recent' && session.status !== 'error' ? (session.snippet || session.activity || '')
        : (session.activity || '')
    if (detail && detail !== session.status_label && detail !== rowStatus(session)) {
      const activity = document.createElement('span')
      activity.className = 'row-activity'
      activity.textContent = ` · ${detail}`
      status.append(activity)
    }
    const where = [session.project || session.source, providerLabel(session)].filter(Boolean).join(' · ')
    if (group === 'earlier') {
      // Two lines are enough for something already dealt with.
      const meta = document.createElement('span')
      meta.className = 'row-activity'
      meta.textContent = where ? ` · ${where}` : ''
      status.append(meta)
      main.append(title, status)
    } else {
      const meta = document.createElement('div')
      meta.className = 'row-meta'
      meta.textContent = where
      main.append(title, status, meta)
    }

    const rowAge = document.createElement('div')
    rowAge.className = 'row-age'
    rowAge.textContent = age(session.age_s)

    row.append(dot, main, rowAge)
    row.addEventListener('click', () => selectSession(sessionKey(session), true))
    row.addEventListener('contextmenu', event => {
      event.preventDefault()
      openRowMenu(sessionKey(session), event.clientX, event.clientY)
    })
    rowsEl.append(row)
  }
  rowsEl.scrollTop = scroll
  if (focusedId) rowsEl.querySelector(`.row[data-id="${CSS.escape(focusedId)}"]`)?.focus()
  const running = sessions.filter(session => sessionGroup(session) === 'running').length
  sessionWindow.textContent = `${sessions.length} sessions${running ? ` · ${running} running` : ''}`
}

function conversationMessages(session) {
  const messages = Array.isArray(session.conversation) ? [...session.conversation] : []
  if (!messages.length && session.preview) {
    messages.push({ role: 'agent', text: session.preview, created_at: '' })
  }
  for (const message of pendingMessages.get(sessionKey(session)) || []) {
    const alreadyVisible = messages.some(item => item.role === 'user' && item.text === message.text)
    if (!alreadyVisible) messages.push(message)
  }
  return messages
}

function messageTime(value) {
  const stamp = Date.parse(value || '')
  if (!Number.isFinite(stamp)) return ''
  const date = new Date(stamp)
  const today = new Date()
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return date.toDateString() === today.toDateString() ? time : `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text)
    if (button) {
      const label = button.textContent
      button.textContent = 'Copied'
      setTimeout(() => { button.textContent = label }, 1200)
    }
  } catch (error) { setActionStatus(`Could not copy: ${error.message}`, true) }
}

conversationEl.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[data-external]')
  if (!anchor) return
  event.preventDefault()
  window.wow.openExternal?.(anchor.dataset.external)
})

function messageNode(session, message) {
  const wrap = document.createElement('div')
  const label = document.createElement('div')
  label.className = 'message-role'
  const bodyEl = document.createElement('div')
  bodyEl.className = 'message-body'
  const copy = document.createElement('button')
  copy.type = 'button'
  copy.className = 'copy-message'
  copy.textContent = 'Copy'
  copy.setAttribute('aria-label', 'Copy message')
  copy.addEventListener('click', () => void copyText(wrap.messageText || '', copy))
  label.append(document.createTextNode(''), copy)
  wrap.append(label, bodyEl)
  updateMessageNode(wrap, session, message)
  return wrap
}

function updateMessageNode(wrap, session, message) {
  const role = message.role === 'user' ? 'user' : 'agent'
  const className = `message ${role}${message.pending ? ' pending' : ''}${message.streaming ? ' streaming' : ''}`
  if (wrap.className !== className) wrap.className = className
  const when = messageTime(message.created_at)
  const labelText = `${role === 'user' ? 'You' : providerLabel(session)}${when ? ` · ${when}` : ''}`
  if (wrap.firstChild.firstChild.nodeValue !== labelText) wrap.firstChild.firstChild.nodeValue = labelText
  // Only touch text that changed, so a streaming reply grows in place and a
  // selection in an earlier message survives the update.
  const bodyEl = wrap.lastChild
  const text = message.text || ''
  if (wrap.messageText !== text) {
    wrap.messageText = text
    if (role === 'agent') window.renderMarkdown(bodyEl, text, { onCopy: copyText })
    else bodyEl.textContent = text
  }
  wrap.dataset.key = message.id || `${role}:${message.created_at || ''}`
}

let conversationSession = null
let workingEl = null

// Runs of tool calls between messages collapse into one line you can open.
function displayItems(messages) {
  const items = []
  for (const message of messages) {
    if (message.role === 'tool') {
      const last = items[items.length - 1]
      if (last?.role === 'steps') last.steps.push(message)
      else items.push({ role: 'steps', id: `steps:${message.id || message.created_at || items.length}`, steps: [message] })
    } else items.push(message)
  }
  return items
}

function stepsNode() {
  const wrap = document.createElement('details')
  const summary = document.createElement('summary')
  const count = document.createElement('span')
  count.className = 'steps-count'
  const last = document.createElement('span')
  last.className = 'steps-last'
  summary.append(count, last)
  const list = document.createElement('ol')
  list.className = 'steps-list'
  wrap.append(summary, list)
  return wrap
}

function updateStepsNode(wrap, item) {
  wrap.className = 'message steps'
  wrap.dataset.key = item.id
  const [count, last] = wrap.firstChild.children
  count.textContent = `${item.steps.length} step${item.steps.length === 1 ? '' : 's'}`
  const latest = item.steps.at(-1).text
  if (last.stepText !== latest) { window.renderInline?.(last, latest); last.stepText = latest }
  const list = wrap.lastChild
  const rows = [...list.children]
  item.steps.forEach((step, index) => {
    let row = rows[index]
    if (!row) { row = document.createElement('li'); list.append(row) }
    if (row.dataset.key !== step.id || row.stepText !== step.text) {
      row.stepText = step.text
      row.dataset.key = step.id
      row.replaceChildren()
      window.renderInline?.(row, step.text)
      row.title = messageTime(step.created_at)
    }
  })
  for (const stale of rows.slice(item.steps.length)) stale.remove()
}

function renderConversation(session) {
  const messages = displayItems(conversationMessages(session))
  const running = ['working', 'waiting', 'starting'].includes(session.status)
  const key = JSON.stringify([sessionKey(session), Array.isArray(session.conversation), providerLabel(session), messages, running, providerAvailable(session), running && session.activity])
  if (key === conversationKey) return
  conversationKey = key

  if (conversationSession !== session.id) {
    conversationSession = session.id
    conversationEl.replaceChildren()
  }
  conversationEl.querySelector('.message-empty')?.remove()
  const oldNodes = [...conversationEl.querySelectorAll(':scope > .message')]
  const available = new Map(oldNodes.map(node => [node.dataset.key, node]))
  const nodes = []
  messages.forEach((message, index) => {
    const role = message.role === 'steps' ? 'steps' : message.role === 'user' ? 'user' : 'agent'
    const key = message.id || `${role}:${message.created_at || index}`
    let node = available.get(key)
    if (!node?.classList.contains(role)) node = null
    if (!node) node = role === 'steps' ? stepsNode() : messageNode(session, message)
    if (role === 'steps') updateStepsNode(node, message)
    else updateMessageNode(node, session, message)
    node.dataset.key = key
    const at = conversationEl.children[index]
    if (at !== node) conversationEl.insertBefore(node, at || null)
    available.delete(key)
    nodes.push(node)
  })
  for (const stale of oldNodes) if (!nodes.includes(stale)) stale.remove()
  // One label per run of messages from the same speaker, as in a chat client.
  nodes.slice(0, messages.length).forEach((node, index) => {
    // Steps do not interrupt a speaker's run: the agent working and then
    // replying is still one turn.
    let back = index - 1
    while (back >= 0 && nodes[back].classList.contains('steps')) back -= 1
    const previous = nodes[back]
    const continued = !node.classList.contains('steps') && Boolean(previous) &&
      previous.classList.contains('user') === node.classList.contains('user')
    node.classList.toggle('continued', continued)
  })

  if (!messages.length) {
    const empty = document.createElement('div')
    empty.className = 'message-empty'
    empty.textContent = Array.isArray(session.conversation) ? 'No messages in this session yet.' : 'Loading conversation…'
    conversationEl.append(empty)
  }
  if (running && providerAvailable(session)) {
    if (!workingEl || !conversationEl.contains(workingEl)) workingEl = workingIndicator(session)
    workingEl.lastChild.textContent = session.activity && session.activity !== session.status_label ? session.activity : 'Working'
    conversationEl.append(workingEl)
  } else { workingEl?.remove(); workingEl = null }
}

function workingIndicator(session) {
  const line = document.createElement('div')
  line.className = 'message-working'
  const dots = document.createElement('span')
  dots.className = 'working-dots'
  dots.append(document.createElement('i'), document.createElement('i'), document.createElement('i'))
  const text = document.createElement('span')
  text.textContent = session.activity && session.activity !== session.status_label ? session.activity : 'Working'
  line.append(dots, text)
  return line
}

let reportedSelection
let lastDetailNews = false
let lastScrollHeight = 0
const jumpLatest = document.getElementById('jump-latest')
jumpLatest.addEventListener('click', () => {
  conversationEl.scrollTo({ top: conversationEl.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
  jumpLatest.hidden = true
})
conversationEl.addEventListener('scroll', () => {
  jumpLatest.hidden = conversationEl.scrollHeight - conversationEl.clientHeight - conversationEl.scrollTop < 60
})

function renderDetail({ preserveScroll = false } = {}) {
  // The bridge sends only the open session's conversation, so it has to know
  // which one that is.
  if (reportedSelection !== selectedId) {
    reportedSelection = selectedId
    window.wow.select?.(selectedId || '')
  }
  const session = selectedSession()
  if (!session) {
    welcomeEl.hidden = false
    detailEl.hidden = true
    lastDetailId = null
    return
  }

  const previousScroll = conversationEl.scrollTop
  const nearBottom = conversationEl.scrollHeight - conversationEl.clientHeight - previousScroll < 60
  const top = conversationEl.getBoundingClientRect().top
  const anchor = [...conversationEl.children].find(node => node.classList.contains('message') && node.getBoundingClientRect().bottom > top)
  const anchorOffset = anchor ? anchor.getBoundingClientRect().top - top : 0
  welcomeEl.hidden = true
  detailEl.hidden = false
  detailProvider.textContent = providerLabel(session)
  detailTitle.textContent = session.title || '(untitled)'
  detailTitle.title = session.title || ''
  detailMeta.textContent = [
    session.project || session.source,
    session.profile,
    age(session.age_s) === 'now' ? 'active now' : `${age(session.age_s)} ago`
  ].filter(Boolean).join(' / ')
  detailStatus.className = `status-pill ${session.status}`
  detailStatus.textContent = rowStatus(session)

  const hasApproval = Boolean(session.approval_request_id)
  const hasInput = Boolean(session.user_input_request_id)
  if (hasApproval || hasInput) {
    attentionEl.hidden = false
    attentionEl.textContent = hasInput
      ? session.user_input_summary || 'The agent needs an answer.'
      : session.approval_summary || 'The agent is waiting for approval.'
  } else {
    attentionEl.hidden = true
  }

  approveButton.hidden = !hasApproval
  declineButton.hidden = !hasApproval
  renderQuestions(session)
  answerButton.hidden = !hasInput
  answerButton.textContent = session.user_input_questions?.length ? 'Send answers' : 'Answer request'
  composerInput.closest('.composer-wrap').hidden = Boolean(session.user_input_questions?.length)
  composerInput.placeholder = hasInput ? 'Answer the request above' : 'Message this session'
  composerInput.disabled = Boolean(session.user_input_questions?.length) || !(session.capabilities || []).includes('reply') && !hasInput
  markReadButton.hidden = !(session.capabilities || []).includes('mark_read') || !Number(session.activity_at || 0) || !hasNews(session)
  stopButton.hidden = !(['working', 'waiting', 'starting', 'needs'].includes(session.status) && (session.capabilities || []).includes('stop'))
  const done = doneAction(session)
  doneButton.hidden = !done
  if (done) {
    doneButton.textContent = done.label
    doneButton.title = `${done.hint} (E)`
  }

  renderConversation(session)
  renderQueue(session)
  fitComposer()
  // Opening a session with news, or watching one when its news arrives, both
  // count as reading it.
  const news = hasNews(session)
  if (lastDetailId !== sessionKey(session) || (news && !lastDetailNews)) scheduleAutoRead(session)
  lastDetailNews = news
  const changed = lastDetailId !== sessionKey(session)
  lastDetailId = sessionKey(session)
  const grew = conversationEl.scrollHeight !== lastScrollHeight
  cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    if (selectedId !== sessionKey(session)) return
    if (changed || !preserveScroll || nearBottom) {
      conversationEl.scrollTop = conversationEl.scrollHeight
      jumpLatest.hidden = true
    } else {
      conversationEl.scrollTop = anchor?.isConnected
        ? previousScroll + anchor.getBoundingClientRect().top - conversationEl.getBoundingClientRect().top - anchorOffset
        : previousScroll
      // Reading back through a reply while the agent keeps writing: say so,
      // but leave the scroll where the reader put it.
      if (grew) jumpLatest.hidden = false
    }
    lastScrollHeight = conversationEl.scrollHeight
  })
}

const doneButton = document.getElementById('done-button')
const rowMenu = document.getElementById('row-menu')

// Everything you can do to a session without opening it. Items only appear
// when the provider supports them, and a run of them is split by separators.
function rowMenuItems(session) {
  const can = kind => (session.capabilities || []).includes(kind)
  const act = kind => () => void menuAction(session, kind)
  const running = ['working', 'waiting', 'starting'].includes(session.status)
  const unread = hasNews(session)
  const done = doneAction(session)
  const items = [{ label: 'Open', run: () => selectSession(sessionKey(session), true) }, 'separator']
  if (unread && can('mark_read')) items.push({ label: 'Mark read', run: act('mark_read') })
  else if (!running && can('mark_unread')) items.push({ label: 'Mark unread', run: act('mark_unread') })
  // E acts on the open session, so the hint only belongs to that one.
  if (done) items.push({ label: done.label, hint: sessionKey(session) === selectedId ? 'E' : '', run: act(done.kind) })
  if (running && can('stop')) items.push({ label: 'Stop turn', danger: true, run: act('stop') })
  items.push('separator')
  items.push({ label: 'Copy title', run: () => void copyText(session.title || '') })
  items.push({ label: 'Copy session ID', run: () => void copyText(session.id) })
  if (session.provider === 't3' && can('archive') && !running) {
    items.push('separator')
    // Archived threads leave the board, so this one asks twice.
    items.push({ label: 'Archive in T3 Code', confirm: 'Click again to archive', danger: true, run: act('archive') })
  }
  return items.filter((item, index, all) => item !== 'separator' || (index > 0 && all[index - 1] !== 'separator' && index < all.length - 1))
}

async function menuAction(session, kind) {
  if (busy || !providerAvailable(session)) { setStatus('Reconnect before using session actions.', true); return }
  const next = kind === 'archive' && sessionKey(session) === selectedId ? neighbourOf(sessionKey(session)) : null
  if (kind === 'stop') queue.hold(session)
  const result = await safeAction({ kind, provider: session.provider, host: session.host || 'local', session_id: session.id,
    text: kind === 'mark_read' ? String(session.activity_at || '') : '' })
  const labels = { mark_read: 'Marked read.', mark_unread: 'Marked unread.', settle: 'Settled in T3 Code.', unsettle: 'Back in T3 Code\'s active list.',
    archive: session.provider === 't3' ? 'Archived in T3 Code.' : 'Archived.', stop: 'Stopping.' }
  if (result.ok) {
    setStatus(labels[kind] || 'Done.')
    if (next) selectSession(next)
    window.wow.command('refresh')
  } else setStatus(result.error || result.message || 'That did not work.', true)
}

let menuReturnFocus = null

function openRowMenu(id, x, y) {
  const session = (board.sessions || []).find(item => sessionKey(item) === id)
  if (!session) return
  menuReturnFocus = document.activeElement
  rowMenu.replaceChildren()
  for (const item of rowMenuItems(session)) {
    if (item === 'separator') {
      const line = document.createElement('div')
      line.className = 'row-menu-separator'
      line.setAttribute('role', 'separator')
      rowMenu.append(line)
      continue
    }
    const button = document.createElement('button')
    button.type = 'button'
    button.className = `row-menu-item${item.danger ? ' danger' : ''}`
    button.setAttribute('role', 'menuitem')
    const label = document.createElement('span')
    label.textContent = item.label
    button.append(label)
    if (item.hint) {
      const hint = document.createElement('kbd')
      hint.textContent = item.hint
      button.append(hint)
    }
    button.addEventListener('click', () => {
      if (item.confirm && !button.dataset.armed) {
        button.dataset.armed = '1'
        label.textContent = item.confirm
        return
      }
      closeRowMenu()
      item.run()
    })
    rowMenu.append(button)
  }
  rowMenu.hidden = false
  // Keep the whole menu inside the window, flipping up or left near an edge.
  const { width, height } = rowMenu.getBoundingClientRect()
  rowMenu.style.left = `${Math.max(6, Math.min(x, window.innerWidth - width - 6))}px`
  rowMenu.style.top = `${Math.max(6, y + height > window.innerHeight - 6 ? y - height : y)}px`
  rowMenu.querySelector('.row-menu-item')?.focus()
}

function closeRowMenu() {
  if (rowMenu.hidden) return
  rowMenu.hidden = true
  if (menuReturnFocus?.isConnected) menuReturnFocus.focus()
  menuReturnFocus = null
}

rowMenu.addEventListener('keydown', event => {
  const items = [...rowMenu.querySelectorAll('.row-menu-item')]
  const index = items.indexOf(document.activeElement)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    event.stopPropagation()
    const step = event.key === 'ArrowDown' ? 1 : -1
    items[(index + step + items.length) % items.length]?.focus()
  } else if (event.key === 'Escape' || event.key === 'Tab') {
    event.preventDefault()
    event.stopPropagation()
    closeRowMenu()
  }
})
document.addEventListener('mousedown', event => { if (!rowMenu.contains(event.target)) closeRowMenu() }, true)
window.addEventListener('blur', closeRowMenu)
rowsEl.addEventListener('scroll', closeRowMenu)

// "Done with this": settle in T3 (it stays in history), archive in Hermes
// (it leaves the list). Not offered while the agent is still working.
function doneAction(session) {
  const capabilities = session.capabilities || []
  if (['working', 'waiting', 'starting'].includes(session.status)) return null
  // Waiting on your answer is not done.
  if (session.approval_request_id || session.user_input_request_id) return null
  if (capabilities.includes('settle')) {
    return session.settled
      ? { kind: 'unsettle', label: 'Unsettle', hint: 'Move back to T3 Code\'s active list' }
      : { kind: 'settle', label: 'Settle', hint: 'Mark done in T3 Code; it stays in history' }
  }
  if (capabilities.includes('archive')) return { kind: 'archive', label: 'Archive', hint: 'Archive in Hermes; it leaves this list' }
  return null
}

async function runDone() {
  const session = selectedSession()
  const done = session && doneAction(session)
  if (!done) return
  const next = done.kind === 'archive' ? neighbourOf(sessionKey(session)) : null
  const result = await runAction({ kind: done.kind, provider: session.provider, host: session.host || 'local', session_id: session.id, text: '' })
  if (result.ok) {
    setActionStatus(done.kind === 'archive' ? 'Archived.' : done.kind === 'settle' ? 'Settled in T3 Code.' : 'Back in T3 Code\'s active list.')
    if (next) selectSession(next)
  }
}

function neighbourOf(id) {
  const sessions = visibleSessions()
  const index = sessions.findIndex(item => sessionKey(item) === id)
  return sessionKey(sessions[index + 1] || sessions[index - 1])
}

function renderAll() {
  renderBadge()
  renderRows()
  renderDetail({ preserveScroll: true })
  updateControls()
}

function selectSession(id, focusComposer = false) {
  saveDraft()
  composerInput.value = drafts.get(id) || ''
  if (selectedId !== id) setActionStatus('')
  selectedId = id
  selectedCache = (board.sessions || []).find(session => sessionKey(session) === id) || null
  renderRows()
  renderDetail()
  if (focusComposer) {
    if (sessionHasQuestions()) questionsEl.querySelector('textarea')?.focus()
    else composerInput.focus()
  }
}

function sessionHasQuestions() { return Boolean(selectedSession()?.user_input_questions?.length) }

function setStatus(text, error = false) {
  boardStatus.textContent = text
  boardStatus.style.color = error ? 'var(--error)' : 'var(--ink-faint)'
  clearTimeout(statusTimer)
  statusTimer = setTimeout(() => {
    boardStatus.textContent = connected ? 'Live connection established.' : 'Waiting for the live bridge.'
    boardStatus.style.color = ''
  }, 4500)
}

function setActionStatus(text, error = false) {
  clearTimeout(actionStatusTimer)
  actionStatus.textContent = text
  actionStatus.style.color = error ? 'var(--error)' : 'var(--gold)'
  if (!text || error) return
  actionStatusTimer = setTimeout(() => {
    if (actionStatus.textContent === text) actionStatus.textContent = ''
  }, 4500)
}

function providerAvailable(provider) {
  if (provider && typeof provider === 'object') {
    if (provider.host && provider.host !== 'local') return connected && !provider.host_offline && board.hosts?.[provider.host] === 'ok'
    provider = provider.provider
  }
  return connected && (!board.providers?.[provider] || board.providers[provider] === 'ok')
}

function updateControls() {
  const session = selectedSession()
  const unavailable = !session || !providerAvailable(session)
  for (const button of [approveButton, declineButton, answerButton, markReadButton, doneButton, stopButton]) {
    button.disabled = busy || unavailable
  }
  sendButton.disabled = busy || !session || composerInput.disabled || !composerInput.value.trim() || Boolean(session.user_input_request_id && unavailable)
  sendNowButton.disabled = busy || unavailable || composerInput.disabled || Boolean(session?.approval_request_id || session?.user_input_request_id) ||
    (!composerInput.value.trim() && !queue.list(session).some(item => ['queued', 'held'].includes(item.state)))
  newSessionStart.disabled = startingSession || addingProject || !providerAvailable('t3')
  addProjectButton.disabled = startingSession || addingProject || !providerAvailable('t3')
  const notice = document.getElementById('connection-notice')
  notice.hidden = !unavailable
  notice.textContent = !connected ? 'Reconnecting to the bridge. Drafts and queued messages are kept.'
    : session ? `${providerLabel(session)} is unavailable. Showing its last update.` : ''
}

function setBusy(next) {
  busy = next
  if (selectedSession()) renderQueue(selectedSession())
  else updateControls()
}

async function runAction(action, { pendingText = '' } = {}) {
  const session = selectedSession()
  if (!session || busy) return { ok: false, error: 'no session selected' }
  if (!providerAvailable(session)) { setActionStatus('Provider offline. Try again after reconnecting.', true); return { ok: false } }
  setBusy(true)

  if (pendingText) {
    const pending = pendingMessages.get(sessionKey(session)) || []
    pending.push({ role: 'user', text: pendingText, pending: true })
    pendingMessages.set(sessionKey(session), pending)
    renderConversation(session)
    conversationEl.scrollTop = conversationEl.scrollHeight
  }

  let result
  try { result = await window.wow.action(action) }
  catch (error) { result = { ok: false, error: error.message } }
  finally { setBusy(false) }

  if (!result.ok) {
    pendingMessages.delete(sessionKey(session))
    if (selectedId === sessionKey(session)) renderConversation(session)
    const message = result.error || result.message || 'Action failed.'
    setActionStatus(message, true)
    setStatus(message, true)
    return result
  }

  setTimeout(() => {
    pendingMessages.delete(sessionKey(session))
    if (selectedId === sessionKey(session)) renderDetail({ preserveScroll: true })
  }, 1500)
  setActionStatus(result.pending ? 'Sent; the agent is starting.' : 'Delivered.')
  if (selectedId === sessionKey(session)) renderConversation(session)
  return result
}

async function sendComposer(immediate = false) {
  const session = selectedSession()
  const text = composerInput.value.trim()
  if (!session || busy || composerInput.disabled) return
  if (immediate && !providerAvailable(session)) { setActionStatus('Reconnect before sending now. Your draft is kept.', true); return }
  if (!text) {
    const item = queue.list(session).find(item => ['queued', 'held'].includes(item.state))
    if (immediate && connected && item) await queue.sendNow(item.id, session)
    return
  }

  if (!session.user_input_request_id) {
    try {
      const item = queue.enqueue(session, text)
      composerInput.value = ''
      saveDraft()
      fitComposer()
      updateControls()
      if (immediate && connected) await queue.sendNow(item.id, session)
      else await queue.update(board, connected)
    } catch (error) { setActionStatus(error.message, true) }
    composerInput.focus()
    return
  }

  const action = session.user_input_request_id
    ? {
        kind: 'answer',
        provider: session.provider,
        host: session.host || 'local',
        session_id: session.id,
        text: `${session.user_input_request_id}~${session.user_input_question_id}~${text}`
      }
    : {
        kind: 'reply',
        provider: session.provider,
        host: session.host || 'local',
        session_id: session.id,
        text
      }

  composerInput.value = ''
  saveDraft()
  const result = await runAction(action, { pendingText: text })
  if (!result.ok) {
    if (selectedId === sessionKey(session)) composerInput.value = [text, composerInput.value].filter(Boolean).join('\n\n')
    else drafts.set(sessionKey(session), [text, drafts.get(sessionKey(session))].filter(Boolean).join('\n\n'))
  }
  saveDraft()
  composerInput.focus()
}

async function safeAction(action) {
  try { return await window.wow.action(action) }
  catch (error) { return { ok: false, error: error.message } }
}

function renderQuestions(session) {
  const questions = session.user_input_questions || []
  const key = JSON.stringify([sessionKey(session), session.user_input_request_id, questions])
  questionsEl.hidden = !questions.length
  if (key === questionsKey) return
  questionsKey = key
  questionsEl.replaceChildren()
  for (const question of questions) {
    const label = document.createElement('label')
    label.textContent = question.question || question.header || 'Answer'
    const input = document.createElement('textarea')
    input.rows = 2
    input.dataset.questionId = question.id
    input.setAttribute('aria-label', label.textContent)
    label.append(input)
    questionsEl.append(label)
    for (const option of question.options || []) {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'button quiet'
      button.textContent = option.label || option
      button.dataset.questionId = question.id
      button.setAttribute('aria-pressed', 'false')
      button.addEventListener('click', () => {
        const value = option.label || option
        if (question.multi_select || question.isMultiple) {
          const selected = new Set(input.value.split(', ').filter(Boolean))
          if (selected.has(value)) selected.delete(value); else selected.add(value)
          input.value = [...selected].join(', ')
          button.setAttribute('aria-pressed', String(selected.has(value)))
        } else {
          input.value = value
          for (const peer of questionsEl.querySelectorAll('button')) {
            if (peer.dataset.questionId === question.id) peer.setAttribute('aria-pressed', String(peer === button))
          }
        }
      })
      questionsEl.append(button)
    }
  }
}

async function sendAnswers(session) {
  const answers = Object.fromEntries([...questionsEl.querySelectorAll('textarea')].map(input => [input.dataset.questionId, input.value.trim()]))
  if (Object.values(answers).some(answer => !answer)) { setActionStatus('Answer each question before sending.', true); return }
  await runAction({ kind: 'answer', provider: session.provider, host: session.host || 'local', session_id: session.id, request_id: session.user_input_request_id, answers })
}

function applyTheme(theme) {
  if (!theme || !theme.variables) return
  for (const [name, value] of Object.entries(theme.variables)) {
    document.documentElement.style.setProperty(name, value)
  }
  document.documentElement.dataset.appearance = theme.appearance || 'dark'
}

function renderProjectOptions() {
  const projects = [...(board.projects || [])]
  for (const project of provisionalProjects.values()) {
    if (!projects.some(item => item.id === project.id)) projects.push(project)
  }
  if (!projects.some(project => project.id === selectedProjectId)) {
    selectedProjectId = projects[0]?.id || null
  }
  projectMenu.replaceChildren()
  for (const project of projects) {
    const option = document.createElement('button')
    option.type = 'button'
    option.className = `dropdown-option${project.id === selectedProjectId ? ' selected' : ''}`
    option.setAttribute('role', 'option')
    option.setAttribute('aria-selected', project.id === selectedProjectId ? 'true' : 'false')
    option.textContent = project.title || project.id
    option.addEventListener('click', () => {
      selectedProjectId = project.id
      projectMenu.hidden = true
      projectTrigger.setAttribute('aria-expanded', 'false')
      renderProjectOptions()
      projectTrigger.focus()
    })
    projectMenu.append(option)
  }
  if (!projects.length) {
    const empty = document.createElement('div')
    empty.className = 'dropdown-empty'
    empty.textContent = 'No projects yet'
    projectMenu.append(empty)
  }
  const selected = projects.find(project => project.id === selectedProjectId)
  projectTriggerLabel.textContent = selected?.title || 'Choose a project'
  selectedProjectPath.textContent = selected?.workspace_root || ''
}

function openNewSession() {
  modalReturnFocus = document.activeElement
  const projects = board.projects || []
  renderProjectOptions()
  updateControls()
  newSessionStatus.textContent = ''
  newSessionPrompt.value = ''
  newSessionModal.hidden = false
  if (projects.length) newSessionPrompt.focus()
  else addProjectButton.focus()
}

function closeNewSession() {
  projectMenu.hidden = true
  projectTrigger.setAttribute('aria-expanded', 'false')
  newSessionModal.hidden = true
  modalReturnFocus?.focus()
}

async function startNewSession() {
  if (startingSession || addingProject || !providerAvailable('t3')) return
  const projectId = selectedProjectId
  const text = newSessionPrompt.value.trim()
  if (!projectId || !text) {
    newSessionStatus.textContent = 'Choose a project and write a prompt.'
    return
  }

  startingSession = true
  updateControls()
  saveDraft()
  newSessionStatus.textContent = 'Starting...'
  const result = await safeAction({
    kind: 'new',
    runtime_mode: document.getElementById('new-session-permissions').value,
    provider: 't3',
    host: 'local',
    session_id: projectId,
    text
  })
  startingSession = false
  updateControls()

  if (!result.ok) {
    newSessionStatus.textContent = result.error || result.message || 'Could not start the session.'
    return
  }

  if (result.thread_id) { selectedId = result.thread_id; selectedCache = null; composerInput.value = '' }
  renderAll()
  closeNewSession()
  setStatus('New session started. It will appear as soon as T3 publishes it.')
}

async function addProject() {
  if (addingProject || startingSession || !providerAvailable('t3')) return
  let chosen
  try { chosen = await window.wow.chooseDirectory() }
  catch (error) { newSessionStatus.textContent = error.message; return }
  if (!chosen?.ok) return
  const workspaceRoot = chosen.path
  const title = workspaceRoot.replaceAll('\\', '/').split('/').filter(Boolean).pop() || 'Project'
  addingProject = true
  updateControls()
  newSessionStatus.textContent = `Adding ${title}...`
  const result = await safeAction({
    kind: 'new_project',
    provider: 't3',
    host: 'local',
    workspace_root: workspaceRoot,
    title
  })
  addingProject = false
  updateControls()
  if (!result.ok) {
    newSessionStatus.textContent = result.error || result.message || 'Could not add the project.'
    return
  }
  selectedProjectId = result.project_id || selectedProjectId
  if (result.project_id) provisionalProjects.set(result.project_id, { id: result.project_id, title, workspace_root: workspaceRoot })
  newSessionStatus.textContent = `${title} added.`
  projectMenu.hidden = true
  projectTrigger.setAttribute('aria-expanded', 'false')
  window.wow.command('refresh')
  renderProjectOptions()
}

function moveSelection(delta) {
  const sessions = visibleSessions()
  if (!sessions.length) return
  const current = Math.max(0, sessions.findIndex(session => sessionKey(session) === selectedId))
  const next = Math.min(sessions.length - 1, Math.max(0, current + delta))
  selectSession(sessionKey(sessions[next]))
  const row = rowsEl.querySelector(`[data-id="${CSS.escape(sessionKey(sessions[next]))}"]`)
  row?.focus({ preventScroll: true })
  row?.scrollIntoView({ block: 'nearest' })
}

searchInput.addEventListener('input', () => {
  search = searchInput.value.trim().toLowerCase()
  renderRows()
})

sendButton.addEventListener('click', () => void sendComposer())
sendNowButton.addEventListener('click', () => void sendComposer(true))
approveButton.addEventListener('click', () => {
  const session = selectedSession()
  if (session) void runAction({ kind: 'approve', provider: session.provider, host: session.host, session_id: session.id, text: session.approval_request_id })
})
declineButton.addEventListener('click', () => {
  const session = selectedSession()
  if (session) void runAction({ kind: 'decline', provider: session.provider, host: session.host, session_id: session.id, text: session.approval_request_id })
})
answerButton.addEventListener('click', () => {
  const session = selectedSession()
  if (session?.user_input_questions?.length) { void sendAnswers(session); return }
  if (session) {
    composerInput.placeholder = session.user_input_summary || 'Type your answer'
    composerInput.focus()
  }
})
doneButton.addEventListener('click', () => void runDone())
markReadButton.addEventListener('click', () => {
  const session = selectedSession()
  if (session) void runAction({ kind: 'mark_read', provider: session.provider, host: session.host, session_id: session.id, text: String(session.activity_at) })
})
stopButton.addEventListener('click', async () => {
  const session = selectedSession()
  if (!session) return
  queue.hold(session)
  stopButton.disabled = true
  setActionStatus('Stopping…')
  await queue.waitForSession(session)
  await runAction({ kind: 'stop', provider: session.provider, host: session.host, session_id: session.id, text: '' })
  stopButton.disabled = false
})

document.getElementById('board-close').addEventListener('click', () => window.wow.command('badge'))
badge.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); window.wow.command('board') }
})
badge.addEventListener('click', event => {
  event.stopPropagation()
  window.wow.command('board')
})
document.getElementById('refresh-button').addEventListener('click', () => window.wow.command('refresh'))
document.getElementById('new-session-button').addEventListener('click', openNewSession)
document.getElementById('new-session-close').addEventListener('click', closeNewSession)
newSessionStart.addEventListener('click', () => void startNewSession())
projectDropdown.addEventListener('keydown', event => {
  const options = [...projectMenu.querySelectorAll('button')]
  if (event.key === 'Escape' && !projectMenu.hidden) {
    event.preventDefault(); event.stopPropagation()
    projectMenu.hidden = true; projectTrigger.setAttribute('aria-expanded', 'false'); projectTrigger.focus()
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && options.length) {
    event.preventDefault()
    projectMenu.hidden = false; projectTrigger.setAttribute('aria-expanded', 'true')
    const current = options.indexOf(document.activeElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
      : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
    options[next].focus()
  }
})
projectTrigger.addEventListener('click', () => {
  const opening = projectMenu.hidden
  projectMenu.hidden = !opening
  projectTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false')
})
addProjectButton.addEventListener('click', () => void addProject())

document.addEventListener('click', event => {
  if (!projectDropdown.contains(event.target)) {
    projectMenu.hidden = true
    projectTrigger.setAttribute('aria-expanded', 'false')
  }
})

document.addEventListener('keydown', event => {
  const writing = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement

  if (!newSessionModal.hidden) {
    if (event.key === 'Tab') {
      const focusable = [...newSessionModal.querySelectorAll('button, textarea, select')].filter(el => !el.disabled && el.getClientRects().length)
      const index = focusable.indexOf(document.activeElement)
      if (event.shiftKey && index <= 0 || !event.shiftKey && index === focusable.length - 1) {
        event.preventDefault()
        focusable[event.shiftKey ? focusable.length - 1 : 0]?.focus()
      }
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeNewSession()
    }
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      void startNewSession()
    }
    return
  }

  if (!rowMenu.hidden) return
  if (event.key === 'Escape') {
    saveDraft()
    window.wow.command('badge')
    return
  }

  if (event.key === '/' && !writing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    searchInput.focus()
    searchInput.select()
    return
  }
  if (event.key.toLowerCase() === 'n' && !writing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    openNewSession()
    return
  }
  if (event.key.toLowerCase() === 'e' && !writing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    void runDone()
    return
  }
  if ((event.key === 'ArrowDown' || event.key === 'j') && !writing) {
    event.preventDefault()
    moveSelection(1)
    return
  }
  if ((event.key === 'ArrowUp' || event.key === 'k') && !writing) {
    event.preventDefault()
    moveSelection(-1)
    return
  }
  if (event.key === 'Enter' && !event.isComposing && event.target === composerInput && !event.shiftKey) {
    event.preventDefault()
    void sendComposer(event.ctrlKey || event.metaKey)
  }
})

window.wow.onRoster(data => {
  board = data || { sessions: [], counts: {}, projects: [] }
  const liveSelected = (board.sessions || []).find(session => sessionKey(session) === selectedId)
  if (liveSelected) selectedCache = liveSelected
  if (!selectedId && (board.sessions || []).length) {
    selectedCache = visibleSessions()[0] || board.sessions[0]
    selectedId = sessionKey(selectedCache)
    composerInput.value = drafts.get(selectedId) || ''
  }
  renderAll()
  void queue.update(board, connected)
})

window.wow.onLive(state => {
  connected = Boolean(state.connected)
  liveStateEl.classList.toggle('offline', !connected)
  liveStateEl.querySelector('span').textContent = connected ? 'live' : 'offline'
  if (state.error && !connected) setStatus(state.error, true)
  else if (connected) {
    const offline = Object.entries(board.providers || {}).filter(([, status]) => status !== 'ok' && status !== 'not_configured').map(([provider]) => provider === 't3' ? 'T3 Code' : 'Hermes')
    boardStatus.textContent = offline.length ? `${offline.join(', ')} unavailable. Queued messages are kept.` : 'Live connection established.'
  }
  renderBadge()
  if (selectedSession()) renderDetail({ preserveScroll: true })
  updateControls()
  void queue.update(board, connected)
})

window.wow.onTheme(theme => applyTheme(theme))

// Demo autoplay: a visible button press, or typing at a person's pace.
window.wow.onDemo?.(async ({ click, type }) => {
  if (click) {
    const button = document.getElementById(click)
    if (!button || button.hidden || button.disabled) return
    button.classList.add('demo-press')
    await new Promise(resolve => setTimeout(resolve, 260))
    button.classList.remove('demo-press')
    button.click()
  } else if (type) {
    composerInput.focus()
    composerInput.value = ''
    for (const char of type) {
      composerInput.value += char
      fitComposer()
      await new Promise(resolve => setTimeout(resolve, 45 + Math.random() * 55))
    }
    await new Promise(resolve => setTimeout(resolve, 350))
    void sendComposer()
  }
})

// The overlay switched between the real bridge and the demo: forget the
// selection and everything drawn from it before the new board arrives.
window.wow.onReset?.(() => {
  closeRowMenu()
  clearTimeout(autoReadTimer)
  board = { sessions: [], counts: {}, projects: [] }
  selectedId = null
  selectedCache = null
  conversationSession = null
  conversationKey = ''
  conversationEl.replaceChildren()
  rowsKey = ''
  reportedSelection = undefined
  lastBadgeScore = null
  pendingMessages.clear()
  composerInput.value = ''
  search = ''
  searchInput.value = ''
  renderAll()
})

window.wow.onFocus(({ sessionId }) => {
  if (sessionId) selectSession(sessionId)
})

window.wow.onMode(({ mode: next }) => {
  mode = next
  body.className = `mode-${next}`
  if (next === 'board') {
    renderDetail()
    scheduleAutoRead(selectedSession())
    if (newSessionModal.hidden) {
      if (sessionHasQuestions()) questionsEl.querySelector('textarea')?.focus()
      else composerInput.focus()
    }
  } else {
    clearTimeout(autoReadTimer)
    reportedBadgeWidth = 0
    requestAnimationFrame(fitBadge)
  }
})

const visibilitySelect = document.getElementById('visibility-policy')
visibilitySelect.addEventListener('change', () => window.wow.command(visibilitySelect.value === 'paused' ? 'pause' : visibilitySelect.value))
window.wow.onVisibility?.(data => { visibilitySelect.value = data.policy; visibilitySelect.title = data.reason })
