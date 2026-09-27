'use strict'
const assert = require('node:assert/strict')
const { MessageQueue } = require('../overlay/message_queue')

function fixture() {
  const data = new Map()
  const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }
  const sent = []
  const queue = new MessageQueue({ storage, send: async action => { sent.push(action); return { ok: true } }, id: () => String(Math.random()) })
  const session = { id: 'one', provider: 't3', status: 'working', conversation: [] }
  const update = () => queue.update({ sessions: [session], providers: { t3: 'ok' } }, true)
  return { queue, session, sent, update, storage }
}

async function main() {
  {
    const { queue, session, sent, update } = fixture()
    queue.enqueue(session, 'first'); queue.enqueue(session, 'second')
    await update(); assert.equal(sent.length, 0)
    session.status = 'finished'; await update(); assert.equal(sent.length, 1)
    await update(); await update(); assert.equal(sent.length, 1, 'stale idle snapshots must not drain the next message')
    session.status = 'working'; await update()
    session.status = 'finished'; await update(); await update()
    assert.deepEqual(sent.map(item => item.text), ['first', 'second'])
  }
  {
    const { queue, session, sent, update } = fixture()
    const item = queue.enqueue(session, 'correction')
    await queue.sendNow(item.id, session)
    await update(); assert.equal(sent.length, 1); assert.equal(sent[0].delivery, 'immediate')
    assert.equal(queue.list(session)[0].state, 'sent')
  }
  {
    const { queue, session, sent, update } = fixture()
    const item = queue.enqueue(session, 'wait')
    session.approval_request_id = 'approval'
    await queue.sendNow(item.id, session); assert.equal(sent.length, 0)
    delete session.approval_request_id
    queue.hold(session); session.status = 'finished'; await update()
    assert.equal(sent.length, 0, 'Stop must hold queued work')
    await queue.sendNow(item.id, session); assert.equal(sent.length, 1)
  }
  {
    const { queue, session, sent, update, storage } = fixture()
    const item = queue.enqueue(session, 'recover')
    queue.send = async () => { throw new Error('connection lost') }
    await queue.sendNow(item.id, session)
    session.status = 'finished'; await update(); assert.equal(sent.length, 0)
    assert.equal(queue.list(session)[0].state, 'held')
    const recovered = new MessageQueue({ storage, send: async () => assert.fail('must not auto retry') })
    await recovered.update({ sessions: [session] }, true)
    assert.equal(recovered.list(session)[0].text, 'recover')
    recovered.items[0].state = 'sending'; recovered.save()
    const interrupted = new MessageQueue({ storage, send: async () => assert.fail('must not retry ambiguous send') })
    assert.equal(interrupted.items[0].state, 'held')
  }
  {
    const { queue, session, sent, update } = fixture()
    session.status = 'finished'
    session.conversation = [{ role: 'user', text: 'repeat' }, { role: 'agent', text: 'old' }]
    queue.enqueue(session, 'repeat'); queue.enqueue(session, 'next')
    await update(); await update(); assert.equal(sent.length, 1)
    session.conversation.push({ role: 'user', text: 'repeat' }, { role: 'agent', text: 'new' })
    await update(); await update(); assert.equal(sent.length, 2, 'fast turns finish between polls')
  }
  {
    const { queue, session, sent } = fixture()
    queue.enqueue(session, 'offline'); session.status = 'finished'
    await queue.update({ sessions: [session] }, false)
    await queue.update({ sessions: [session], providers: { t3: 'offline' } }, true)
    assert.equal(sent.length, 0)
    const other = { ...session, provider: 'hermes' }
    assert.equal(queue.list(other).length, 0)
  }
  {
    const { queue, session } = fixture()
    let resolve
    queue.send = () => new Promise(done => { resolve = done })
    const item = queue.enqueue(session, 'race')
    const send = queue.sendNow(item.id, session)
    assert.equal(queue.remove(item.id), null)
    await queue.sendNow(item.id, session)
    resolve({ ok: true }); await send
    assert.equal(queue.items.length, 1)
  }
  {
    const { queue, session, sent, update } = fixture()
    session.status = 'finished'
    delete session.conversation
    session.message_receipts = []
    const first = queue.enqueue(session, 'fast background')
    queue.enqueue(session, 'next background')
    await update()
    session.message_receipts = [{ id: 'u', role: 'user', digest: first.digest }, { id: 'a', role: 'agent' }]
    await update(); await update()
    assert.equal(sent.length, 2, 'background fast-turn receipts release the next message')
  }
  console.log('Message queue: FIFO, send now, approvals, stop, reconnect, restart, duplicate text, fast turns, in-flight races passed')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
