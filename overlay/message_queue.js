'use strict'

// Persist before dispatch. An interrupted send is held for review, never retried
// automatically: the provider might have received it before the connection died.
class MessageQueue {
  constructor({ storage, send, changed = () => {}, id = () => crypto.randomUUID() }) {
    this.storage = storage
    this.send = send
    this.changed = changed
    this.id = id
    const raw = storage.getItem('message-queue-v1') || '[]'
    try {
      this.items = JSON.parse(raw)
      if (!Array.isArray(this.items) || this.items.some(item => !item?.action || !item.id)) throw new Error('Invalid queue')
    } catch {
      storage.setItem('message-queue-recovery', raw)
      this.items = []
    }
    this.inflight = new Set()
    this.requests = new Map()
    for (const item of this.items) {
      if (item.state === 'sending') {
        item.state = 'held'
        item.error = 'Delivery uncertain after restart. Check the conversation before retrying.'
      }
    }
    this.save()
  }

  key(session) { return JSON.stringify([session.provider, session.host || 'local', session.id || session.session_id]) }
  list(session) { return this.items.filter(item => this.key(item.action) === this.key(session)) }
  save() {
    this.storage.setItem('message-queue-v1', JSON.stringify(this.items))
    this.changed()
  }
  blocked(session) {
    return !session || session.status === 'needs' || session.status === 'error' ||
      Boolean(session.approval_request_id || session.user_input_request_id)
  }
  active(session) { return ['working', 'waiting', 'starting'].includes(session.status) }
  count(session, text) {
    return (session.conversation || []).filter(message => message.role === 'user' && message.text === text).length
  }
  enqueue(session, text) {
    const item = {
      id: this.id(), state: 'queued', text,
      action: { kind: 'reply', provider: session.provider, host: session.host || 'local', session_id: session.id, text }
    }
    this.items.push(item)
    try { this.save() } catch (error) { this.items.pop(); throw error }
    return item
  }
  remove(id) {
    const item = this.items.find(item => item.id === id)
    if (!item || ['sending', 'sent'].includes(item.state)) return null
    this.items = this.items.filter(entry => entry !== item)
    this.save()
    return item
  }
  hold(session) {
    for (const item of this.list(session)) {
      if (item.state === 'queued') { item.state = 'held'; item.error = 'Paused by Stop turn. Send now to resume.' }
    }
    this.save()
  }
  async deliver(item, session, immediate = false) {
    const key = this.key(session)
    if (this.inflight.has(key) || session.approval_request_id || session.user_input_request_id ||
        (!immediate && this.blocked(session)) || !['queued', 'held'].includes(item.state)) return
    this.inflight.add(key)
    item.state = 'sending'
    item.baseline = this.count(session, item.text)
    item.baselineIds = (session.message_receipts || session.conversation || []).map(message => message.id).filter(Boolean)
    item.sawActive = false
    delete item.error
    try {
      this.save()
      const request = Promise.resolve(this.send({ ...item.action, ...(immediate ? { delivery: 'immediate' } : { delivery: 'queued' }) }))
      this.requests.set(key, request)
      const result = await request
      const webcrypto = globalThis.crypto || (typeof require === 'function' ? require('node:crypto').webcrypto : null)
      if (webcrypto?.subtle) {
        const hash = await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(item.text))
        item.digest = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
      }
      if (!result?.ok) throw new Error(result?.error || result?.message || 'Delivery failed. Check the conversation before retrying.')
      item.state = 'sent'
      item.messageId = result.message_id
      item.receipt = result.pending ? 'Starting' : 'Sent'
    } catch (error) {
      item.state = 'held'
      item.error = error.message
    } finally {
      this.requests.delete(key)
      this.inflight.delete(key)
      this.save()
    }
  }
  async waitForSession(session) {
    try { await this.requests.get(this.key(session)) } catch {}
  }
  async sendNow(id, session) {
    const item = this.list(session).find(item => item.id === id)
    if (item) await this.deliver(item, session, true)
  }
  async update(board, connected) {
    if (!connected) return
    const sends = []
    for (const session of board.sessions || []) {
      if (session.host_offline || (session.host && session.host !== 'local' && board.hosts?.[session.host] !== 'ok')) continue
      if ((!session.host || session.host === 'local') && board.providers?.[session.provider] && board.providers[session.provider] !== 'ok') continue
      const items = this.list(session)
      if (!items.length || this.inflight.has(this.key(session))) continue
      const head = items[0]
      if (head.state === 'sent') {
        if (this.active(session)) head.sawActive = true
        const visible = (session.conversation || []).some(message => message.role === 'user' &&
          (head.messageId ? message.id === head.messageId :
            message.text === head.text && message.id && !(head.baselineIds || []).includes(message.id))) ||
          this.count(session, head.text) > head.baseline
        const messages = session.message_receipts || session.conversation || []
        const receiptIndex = messages.findIndex(message => message.role === 'user' &&
          (head.messageId ? message.id === head.messageId : head.digest && message.digest === head.digest &&
            message.id && !(head.baselineIds || []).includes(message.id)))
        const receiptFinished = receiptIndex >= 0 && messages.slice(receiptIndex + 1).some(message => ['agent', 'assistant'].includes(message.role))
        // A fast turn can start and finish between polls. Its persisted user row
        // and subsequent assistant response also prove the turn boundary.
        const fastFinished = visible && messages.at(-1)?.role !== 'user'
        if (!this.active(session) && !this.blocked(session) && (head.sawActive || fastFinished || receiptFinished)) {
          this.items = this.items.filter(item => item !== head)
          this.save()
        }
        // Never release two messages using the same pre-dispatch snapshot.
        continue
      }
      if (head.state === 'queued' && !this.active(session) && !this.blocked(session)) {
        sends.push(this.deliver(head, session))
      }
    }
    await Promise.all(sends)
  }
}

if (typeof module !== 'undefined') module.exports = { MessageQueue }
else window.MessageQueue = MessageQueue
