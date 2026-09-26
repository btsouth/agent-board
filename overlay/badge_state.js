'use strict'
function badgeState(board, connected, queued = 0) {
  if (!connected) return { label: 'Reconnecting…', detail: 'Messages and drafts are kept', attention: false }
  const sessions = board.sessions || []
  const available = session => !board.providers?.[session.provider] || board.providers[session.provider] === 'ok'
  const running = sessions.filter(session => available(session) && (session.status === 'working' || session.status === 'starting'))
  const requests = sessions.filter(session => available(session) && (session.approval_request_id || session.user_input_request_id))
  const latest = sessions.filter(session => ['finished', 'reply'].includes(session.status))
    .sort((a, b) => Number(b.activity_at || 0) - Number(a.activity_at || 0))[0]
  const elapsed = seconds => seconds < 60 ? 'just now' : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`
  const label = requests.length ? `${requests.length} need${requests.length === 1 ? 's' : ''} you`
    : running.length ? `${running.length} running` : 'Idle'
  let detail = running[0]?.title || 'Ready when you are'
  if (latest) detail = `Last reply ${elapsed(Number(latest.age_s || 0))} · ${latest.title || 'Untitled session'}`
  if (requests.length) detail = `${requests.length} pending request${requests.length === 1 ? '' : 's'} · ${requests[0].title || 'Agent'}`
  if (queued) detail = `${queued} queued · ${detail}`
  return { label, detail, attention: requests.length > 0, running: running.length }
}
if (typeof module !== 'undefined') module.exports = { badgeState }
else window.badgeState = badgeState
