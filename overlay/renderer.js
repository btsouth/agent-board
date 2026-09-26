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
let lastDetailId = null
let statusTimer = null
let selectedProjectId = null
const provisionalProjects = new Map()
const pendingMessages = new Map()
let savedDrafts = {}
try { savedDrafts = JSON.parse(localStorage.getItem('composer-drafts-v1') || '{}') } catch {}
const drafts = new Map(Object.entries(savedDrafts))
function saveDraft() {
  if (!selectedId) return
  drafts.set(selectedId, composerInput.value)
  try { localStorage.setItem('composer-drafts-v1', JSON.stringify(Object.fromEntries(drafts))) }
  catch { setStatus('Draft storage is full. Keep this window open until your message is sent.', true) }
}
composerInput.addEventListener('input', saveDraft)
window.addEventListener('beforeunload', saveDraft)
const queue = new window.MessageQueue({
  storage: localStorage,
  send: action => window.wow.action(action),
  changed: () => queueMicrotask(() => { renderBadge(); if (selectedSession()) renderQueue(selectedSession()) })
})

function renderQueue(session) {
  queueEl.replaceChildren()
  const items = queue.list(session)
  queueEl.hidden = items.length === 0
  const active = queue.active(session) || items.length > 0
  sendButton.textContent = session.user_input_request_id ? 'Answer' : active ? 'Queue message' : 'Send'
  sendNowButton.hidden = !active || Boolean(session.user_input_request_id)
  sendNowButton.disabled = !connected || Boolean(session.approval_request_id || session.user_input_request_id)
  for (const item of items) {
    const row = document.createElement('div')
    row.className = 'queued-message'
    const text = document.createElement('div')
    text.className = 'queued-text'
    const label = item.state === 'queued' ? 'Queued for next turn' : item.state === 'held' ? 'Needs review' : item.state === 'sending' ? 'Sending…' : item.receipt || 'Sent'
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
      now.disabled = !connected || Boolean(session.approval_request_id || session.user_input_request_id) || queue.inflight.has(queue.key(session))
      now.addEventListener('click', () => void queue.sendNow(item.id, session))
      const edit = document.createElement('button')
      edit.className = 'button quiet'
      edit.textContent = 'Edit'
      edit.addEventListener('click', () => {
        const removed = queue.remove(item.id)
        if (removed) { composerInput.value = [composerInput.value, removed.text].filter(Boolean).join('\n\n'); saveDraft() }
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
// anything blocked on you first, then live work, then replies you have not
// read, then everything you have already seen.
const GROUPS = [
  { key: 'needs', label: 'Needs you' },
  { key: 'running', label: 'Running' },
  { key: 'unread', label: 'New replies' },
  { key: 'seen', label: 'Seen' }
]

function sessionGroup(session) {
  if (session.approval_request_id || session.user_input_request_id || ['needs', 'error'].includes(session.status)) return 'needs'
  if (['working', 'waiting', 'starting'].includes(session.status)) return 'running'
  if (session.status === 'reply') return 'unread'
  return 'seen'
}

function rowStatus(session) {
  if (session.approval_request_id) return 'Needs approval'
  if (session.user_input_request_id) return 'Needs an answer'
  const labels = { needs: 'Asked you something', error: 'Error', working: 'Running', waiting: 'Starting', starting: 'Starting', reply: 'Unread reply', finished: 'Finished', idle: 'Idle' }
  return labels[session.status] || session.status_label || session.status || ''
}

function visibleSessions() {
  const order = Object.fromEntries(GROUPS.map((group, index) => [group.key, index]))
  return [...(board.sessions || [])]
    .filter(sessionMatchesSearch)
    .sort((left, right) => (order[sessionGroup(left)] - order[sessionGroup(right)]) ||
      Number(right.activity_at || 0) - Number(left.activity_at || 0))
}

function selectedSession() {
  return (board.sessions || []).find(session => session.id === selectedId) || selectedCache
}

function renderBadge() {
  const queued = queue.items.filter(item => ['queued', 'held'].includes(item.state)).length
  const state = window.badgeState(board, connected, queued)
  badgeText.textContent = state.label
  badge.classList.toggle('attention', state.attention)
  badge.classList.toggle('running', Boolean(state.running))
  badgeText.classList.toggle('hot', state.attention)
  badge.title = `${state.label}\n${state.detail}\nSuper+Alt+C to toggle`
}

let rowsKey = ''

function renderRows() {
  const sessions = visibleSessions()
  // A roster update arrives several times a second while agents run; rebuilding
  // identical rows would reset hover and focus for nothing.
  const key = JSON.stringify([selectedId, sessions.map(session => [session.id, session.status, session.title, session.activity,
    session.age_s < 60 ? 0 : Math.floor(session.age_s / 60), session.approval_request_id, session.user_input_request_id, session.project])])
  if (key === rowsKey && (selectedId || !sessions.length)) return
  rowsKey = key
  const focusedId = document.activeElement?.closest?.('.row')?.dataset.id
  const scroll = rowsEl.scrollTop
  rowsEl.replaceChildren()
  emptyEl.hidden = sessions.length > 0

  if (!selectedId && sessions.length) {
    selectedId = sessions[0].id
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
    row.dataset.id = session.id
    row.tabIndex = 0
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSession(session.id, true) }
    })
    row.className = `row ${group}${session.id === selectedId ? ' selected' : ''}`
    row.setAttribute('role', 'option')
    row.setAttribute('aria-selected', session.id === selectedId ? 'true' : 'false')

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
    const detail = group === 'seen' ? '' : (session.activity || '')
    if (detail && detail !== session.status_label && detail !== rowStatus(session)) {
      const activity = document.createElement('span')
      activity.className = 'row-activity'
      activity.textContent = ` · ${detail}`
      status.append(activity)
    }
    const where = [session.project || session.source, providerLabel(session)].filter(Boolean).join(' · ')
    if (group === 'seen') {
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
    row.addEventListener('click', () => selectSession(session.id, true))
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
  for (const message of pendingMessages.get(session.id) || []) {
    const alreadyVisible = messages.some(item => item.role === 'user' && item.text === message.text)
    if (!alreadyVisible) messages.push(message)
  }
  return messages
}

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
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(bodyEl.textContent || ''); copy.textContent = 'Copied' }
    catch (error) { setActionStatus(`Could not copy: ${error.message}`, true) }
  })
  label.append(document.createTextNode(''), copy)
  wrap.append(label, bodyEl)
  updateMessageNode(wrap, session, message)
  return wrap
}

function updateMessageNode(wrap, session, message) {
  const role = message.role === 'user' ? 'user' : 'agent'
  const className = `message ${role}${message.pending ? ' pending' : ''}${message.streaming ? ' streaming' : ''}`
  if (wrap.className !== className) wrap.className = className
  const labelText = role === 'user' ? 'You' : providerLabel(session)
  if (wrap.firstChild.firstChild.nodeValue !== labelText) wrap.firstChild.firstChild.nodeValue = labelText
  // Only touch text that changed, so a streaming reply grows in place and a
  // selection in an earlier message survives the update.
  const bodyEl = wrap.lastChild
  if (bodyEl.textContent !== (message.text || '')) bodyEl.textContent = message.text || ''
  wrap.dataset.key = message.id || `${role}:${message.created_at || ''}`
}

let conversationSession = null
let workingEl = null

function renderConversation(session) {
  const messages = conversationMessages(session)
  const running = ['working', 'waiting', 'starting'].includes(session.status)
  const key = JSON.stringify([session.id, messages, running, running && session.activity])
  if (key === conversationKey) return
  conversationKey = key

  if (conversationSession !== session.id) {
    conversationSession = session.id
    conversationEl.replaceChildren()
  }
  conversationEl.querySelector('.message-empty')?.remove()
  workingEl?.remove()
  workingEl = null

  const nodes = [...conversationEl.querySelectorAll(':scope > .message')]
  messages.forEach((message, index) => {
    const role = message.role === 'user' ? 'user' : 'agent'
    const node = nodes[index]
    const key = message.id || `${role}:${message.created_at || ''}`
    // Reuse the node when it is the same message; a window that slid (older
    // messages dropped off the top) or a different message rebuilds from here.
    if (node && node.dataset.key === key && node.classList.contains(role)) {
      updateMessageNode(node, session, message)
    } else {
      for (const stale of nodes.slice(index)) stale.remove()
      nodes.length = index
      const fresh = messageNode(session, message)
      conversationEl.append(fresh)
      nodes.push(fresh)
    }
  })
  for (const stale of nodes.slice(messages.length)) stale.remove()

  if (!messages.length) {
    const empty = document.createElement('div')
    empty.className = 'message-empty'
    empty.textContent = 'No messages in this session yet.'
    conversationEl.append(empty)
  }
  if (running) {
    workingEl = workingIndicator(session)
    conversationEl.append(workingEl)
  }
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

function renderDetail({ preserveScroll = false } = {}) {
  const session = selectedSession()
  if (!session) {
    welcomeEl.hidden = false
    detailEl.hidden = true
    lastDetailId = null
    return
  }

  const previousScroll = conversationEl.scrollTop
  const nearBottom = conversationEl.scrollHeight - conversationEl.clientHeight - previousScroll < 60
  welcomeEl.hidden = true
  detailEl.hidden = false
  detailProvider.textContent = providerLabel(session)
  detailTitle.textContent = session.title || '(untitled)'
  detailMeta.textContent = [
    session.project || session.source,
    session.profile,
    `${age(session.age_s)} ago`
  ].filter(Boolean).join(' / ')
  detailStatus.className = `status-pill ${session.status}`
  detailStatus.textContent = session.status_label || session.status

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
  composerInput.placeholder = hasInput ? 'Answer the request above' : 'Message this session'
  composerInput.disabled = Boolean(session.user_input_questions?.length)
  markReadButton.hidden = !Number(session.activity_at || 0)
  stopButton.hidden = !(['working', 'waiting', 'needs'].includes(session.status) && (session.capabilities || []).includes('stop'))

  renderConversation(session)
  renderQueue(session)
  const changed = lastDetailId !== session.id
  lastDetailId = session.id
  requestAnimationFrame(() => {
    if (changed || !preserveScroll || nearBottom) conversationEl.scrollTop = conversationEl.scrollHeight
    else conversationEl.scrollTop = previousScroll
  })
}

function renderAll() {
  renderBadge()
  renderRows()
  renderDetail({ preserveScroll: true })
}

function selectSession(id, focusComposer = false) {
  saveDraft()
  composerInput.value = drafts.get(id) || ''
  selectedId = id
  selectedCache = (board.sessions || []).find(session => session.id === id) || null
  renderRows()
  renderDetail()
  if (focusComposer) composerInput.focus()
}

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
  actionStatus.textContent = text
  actionStatus.style.color = error ? 'var(--error)' : 'var(--gold)'
  if (!text) return
  setTimeout(() => {
    if (actionStatus.textContent === text) actionStatus.textContent = ''
  }, 4500)
}

function setBusy(next) {
  busy = next
  sendButton.disabled = next
  approveButton.disabled = next
  declineButton.disabled = next
  answerButton.disabled = next
  markReadButton.disabled = next
  stopButton.disabled = next
}

async function runAction(action, { pendingText = '' } = {}) {
  const session = selectedSession()
  if (!session || busy) return { ok: false, error: 'no session selected' }
  if (!connected) { setActionStatus('Bridge offline. Try again after reconnecting.', true); return { ok: false } }
  setBusy(true)

  if (pendingText) {
    const pending = pendingMessages.get(session.id) || []
    pending.push({ role: 'user', text: pendingText, pending: true })
    pendingMessages.set(session.id, pending)
    renderConversation(session)
    conversationEl.scrollTop = conversationEl.scrollHeight
  }

  let result
  try { result = await window.wow.action(action) }
  catch (error) { result = { ok: false, error: error.message } }
  finally { setBusy(false) }

  if (!result.ok) {
    pendingMessages.delete(session.id)
    if (selectedId === session.id) renderConversation(session)
    const message = result.error || result.message || 'Action failed.'
    setActionStatus(message, true)
    setStatus(message, true)
    return result
  }

  setTimeout(() => {
    pendingMessages.delete(session.id)
    if (selectedId === session.id) renderDetail({ preserveScroll: true })
  }, 1500)
  setActionStatus(result.pending ? 'Sent; the agent is starting.' : 'Delivered.')
  if (selectedId === session.id) renderConversation(session)
  return result
}

async function sendComposer(immediate = false) {
  const session = selectedSession()
  const text = composerInput.value.trim()
  if (!session || busy || composerInput.disabled) return
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
    if (selectedId === session.id) composerInput.value = [text, composerInput.value].filter(Boolean).join('\n\n')
    else drafts.set(session.id, [text, drafts.get(session.id)].filter(Boolean).join('\n\n'))
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
  const key = JSON.stringify([session.id, session.user_input_request_id, questions])
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
      button.addEventListener('click', () => {
        const value = option.label || option
        if (question.multi_select || question.isMultiple) {
          const selected = new Set(input.value.split(', ').filter(Boolean))
          if (selected.has(value)) selected.delete(value); else selected.add(value)
          input.value = [...selected].join(', ')
          button.setAttribute('aria-pressed', String(selected.has(value)))
        } else input.value = value
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
  if (!selectedProjectId && projects.length) {
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
  const projects = board.projects || []
  renderProjectOptions()
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
}

async function startNewSession() {
  const projectId = selectedProjectId
  const text = newSessionPrompt.value.trim()
  if (!projectId || !text) {
    newSessionStatus.textContent = 'Choose a project and write a prompt.'
    return
  }

  newSessionStart.disabled = true
  newSessionStatus.textContent = 'Starting...'
  const previousId = selectedId
  selectedId = null
  const result = await safeAction({
    kind: 'new',
    provider: 't3',
    host: 'local',
    session_id: projectId,
    text
  })
  newSessionStart.disabled = false

  if (!result.ok) {
    selectedId = previousId
    newSessionStatus.textContent = result.error || result.message || 'Could not start the session.'
    return
  }

  if (result.thread_id) { selectedId = result.thread_id; selectedCache = null; composerInput.value = '' }
  closeNewSession()
  setStatus('New session started. It will appear as soon as T3 publishes it.')
}

async function addProject() {
  let chosen
  try { chosen = await window.wow.chooseDirectory() }
  catch (error) { newSessionStatus.textContent = error.message; return }
  if (!chosen?.ok) return
  const workspaceRoot = chosen.path
  const title = workspaceRoot.replaceAll('\\', '/').split('/').filter(Boolean).pop() || 'Project'
  addProjectButton.disabled = true
  newSessionStatus.textContent = `Adding ${title}...`
  const result = await safeAction({
    kind: 'new_project',
    provider: 't3',
    host: 'local',
    workspace_root: workspaceRoot,
    title
  })
  addProjectButton.disabled = false
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
  const current = Math.max(0, sessions.findIndex(session => session.id === selectedId))
  const next = Math.min(sessions.length - 1, Math.max(0, current + delta))
  selectSession(sessions[next].id)
}

searchInput.addEventListener('input', () => {
  search = searchInput.value.trim().toLowerCase()
  renderRows()
  renderDetail()
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
badge.addEventListener('click', event => {
  event.stopPropagation()
  window.wow.command('board')
})
document.getElementById('refresh-button').addEventListener('click', () => window.wow.command('refresh'))
document.getElementById('new-session-button').addEventListener('click', openNewSession)
document.getElementById('new-session-close').addEventListener('click', closeNewSession)
newSessionStart.addEventListener('click', () => void startNewSession())
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

  if (event.key === 'Escape') {
    saveDraft()
    window.wow.command('badge')
    return
  }

  if (event.key === '/' && !writing) {
    event.preventDefault()
    searchInput.focus()
    searchInput.select()
    return
  }
  if (event.key.toLowerCase() === 'n' && !writing) {
    event.preventDefault()
    openNewSession()
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
  const liveSelected = (board.sessions || []).find(session => session.id === selectedId)
  if (liveSelected) selectedCache = liveSelected
  if (!selectedId && (board.sessions || []).length) {
    selectedId = board.sessions[0].id
    selectedCache = board.sessions[0]
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
    const offline = Object.entries(board.providers || {}).filter(([, status]) => status !== 'ok').map(([provider]) => provider === 't3' ? 'T3 Code' : 'Hermes')
    boardStatus.textContent = offline.length ? `${offline.join(', ')} unavailable. Queued messages are kept.` : 'Live connection established.'
  }
  renderBadge()
  if (selectedSession()) renderQueue(selectedSession())
  void queue.update(board, connected)
})

window.wow.onTheme(theme => applyTheme(theme))

window.wow.onFocus(({ sessionId }) => {
  if (sessionId) selectSession(sessionId)
})

window.wow.onMode(({ mode: next }) => {
  mode = next
  body.className = `mode-${next}`
  if (next === 'board') { renderDetail(); if (newSessionModal.hidden) composerInput.focus() }
})
