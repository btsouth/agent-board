'use strict'
const assert = require('node:assert/strict')
const fs = require('node:fs')
const fsp = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const vm = require('node:vm')
const { randomUUID } = require('node:crypto')
const source = fs.readFileSync(path.join(__dirname, '../agentboard/providers/t3_provider.mjs'), 'utf8')
const context = vm.createContext({ fsp, randomUUID, process, setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 30)), clearTimeout, clearInterval, WebSocket: { OPEN: 1 }, readJson: () => ({}), STATE_PATH: '/unused', firstLine: text => text, readDefaultModelSelection: () => ({ instanceId: 'test', model: 'test' }) })
vm.runInContext(source.slice(source.indexOf('const writes ='), source.indexOf('function truncate')) +
  source.slice(source.indexOf('class T3RpcClient'), source.indexOf('function readDefaultModelSelection')) +
  '\nglobalThis.exports = { writeJsonAtomic, T3RpcClient, Bridge };', context)
const { writeJsonAtomic, T3RpcClient, Bridge } = context.exports
;(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'provider-runtime-'))
  try {
    const file = path.join(temp, 'state.json')
    await Promise.all(Array.from({ length: 40 }, (_, revision) => writeJsonAtomic(file, { revision })))
    assert.equal(JSON.parse(fs.readFileSync(file)).revision, 39)
    assert.equal(fs.readdirSync(temp).length, 1)
    // Publishing saves state every time; only a change may rewrite the file.
    context.STATE_PATH = path.join(temp, 'provider-state.json')
    const saver = new Bridge()
    await saver.saveState()
    const written = fs.statSync(context.STATE_PATH).ino
    await saver.saveState()
    assert.equal(fs.statSync(context.STATE_PATH).ino, written)
    saver.state.seen.thread = 1
    await saver.saveState()
    assert.notEqual(fs.statSync(context.STATE_PATH).ino, written)
    assert.equal(JSON.parse(fs.readFileSync(context.STATE_PATH)).seen.thread, 1)
  } finally { fs.rmSync(temp, { recursive: true, force: true }) }
  const rpc = new T3RpcClient({}, '')
  rpc.socket = { readyState: 1, send() {}, close() {} }
  await assert.rejects(rpc.request('test', {}), error => error.uncertain === true)
  assert.equal(rpc.pending.size, 0)
  const pending = rpc.request('test', {}); rpc.close()
  await assert.rejects(pending, error => error.uncertain === true)
  assert.equal(rpc.pending.size, 0)
  const bridge = new Bridge()
  bridge.connected = true
  bridge.client = { request: async (_method, command) => { commands.push(command) } }
  bridge.projects.set('project', { id: 'project', title: 'Project' })
  const commands = []
  for (const mode of [undefined, 'full-access']) {
    await bridge.dispatch({ kind: 'new', sessionId: 'project', text: 'Task', runtimeMode: mode })
    assert.equal(commands.at(-1).runtimeMode, mode || 'approval-required')
    assert.equal(commands.at(-1).bootstrap.createThread.runtimeMode, mode || 'approval-required')
  }
  await assert.rejects(bridge.dispatch({ kind: 'new', sessionId: 'project', text: 'Task', runtimeMode: 'unknown' }))
  console.log('Provider runtime: ordered atomic writes, bounded RPCs, disconnect cleanup, explicit new-session permissions passed')
})().catch(error => { console.error(error); process.exitCode = 1 })
