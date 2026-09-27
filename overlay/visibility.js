'use strict'

class Visibility {
  constructor(policy = 'manual', now = () => Date.now()) {
    this.policy = ['automatic', 'manual', 'paused'].includes(policy) ? policy : 'manual'
    this.now = now
    this.game = { running: false }
    this.receivedAt = 0
  }
  update(game) {
    if (this.policy === 'paused' && this.game.running && !game.running) this.policy = 'automatic'
    this.game = game
    this.receivedAt = this.now()
  }
  set(policy) {
    if (!['automatic', 'manual', 'paused'].includes(policy)) return false
    this.policy = policy
    return true
  }
  get reason() {
    if (this.policy === 'manual') return 'manual'
    if (this.policy === 'paused') return 'paused until WoW exits'
    if (!this.receivedAt || this.now() - this.receivedAt > 15000) return 'waiting for bridge'
    return this.game.running ? 'WoW running' : 'WoW stopped'
  }
  get visible() { return this.reason === 'manual' || this.reason === 'WoW running' }
}
module.exports = { Visibility }
