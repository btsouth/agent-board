// Run in the T3 collaborative preview against the synthetic fixture page.
(async () => {
  const checks = []
  const check = (ok, name) => { if (!ok) throw new Error(name); checks.push(name) }
  const click = id => document.getElementById(id).click()
  const input = (id, value) => { const node = document.getElementById(id); node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })) }
  const tick = () => new Promise(resolve => setTimeout(resolve, 50))
  click('new-session-button')
  check(document.getElementById('new-session-permissions').value === 'approval-required', 'new sessions show approval-required')
  input('new-session-prompt', 'Fixture permission check')
  click('new-session-start'); await tick()
  check(actions.at(-1).runtime_mode === 'approval-required', 'new session dispatch includes chosen permissions')
  click('new-session-button')
  document.getElementById('new-session-permissions').value = 'full-access'
  input('new-session-prompt', 'Fixture full access check')
  const rect = document.getElementById('new-session-start').getBoundingClientRect()
  check(rect.bottom <= innerHeight && rect.top > 0, 'start control fits minimum height')
  click('new-session-start'); await tick()
  check(actions.at(-1).runtime_mode === 'full-access', 'full access requires explicit selection')
  const local = { id: 'duplicate-session', provider: 'hermes', host: 'local', title: 'Local fixture', status: 'reply', capabilities: ['reply'], conversation: [{ id: 'local-text', role: 'agent', text: 'Local reply' }] }
  const remote = { ...local, host: 'remote', title: 'Remote fixture', conversation: [{ id: 'remote-text', role: 'agent', text: 'Remote reply' }] }
  handlers.roster({ sessions: [local, remote], providers: { hermes: 'not_configured', t3: 'ok' }, hosts: { local: 'ok', remote: 'ok' } })
  handlers.live({ connected: true })
  const row = title => [...document.querySelectorAll('.row')].find(node => node.textContent.includes(title))
  row('Remote fixture').click()
  check(document.getElementById('conversation').textContent.includes('Remote reply'), 'remote duplicate ID selects remote transcript')
  check(!document.getElementById('composer-input').disabled, 'remote actions do not depend on local provider configuration')
  input('composer-input', 'remote draft')
  row('Local fixture').click(); input('composer-input', 'local draft')
  row('Remote fixture').click()
  check(document.getElementById('composer-input').value === 'remote draft', 'remote and local drafts are separate')
  check(!document.getElementById('board-status').textContent.includes('Hermes unavailable'), 'unconfigured provider is not reported as failed')
  handlers.roster({ sessions: [local, { ...remote, host_offline: true }], providers: { hermes: 'not_configured' }, hosts: { remote: 'offline' } })
  check(document.getElementById('send-now-button').disabled, 'offline remote cannot send immediately')
  handlers.roster(fixture); handlers.live({ connected: true }); handlers.focus({ sessionId: 'demo-uploader' })
  click('new-session-button')
  return { passed: checks }
})()
