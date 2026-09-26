'use strict'
const assert = require('node:assert/strict')
const { badgeState } = require('../overlay/badge_state')
const stale = {sessions:[{status:'needs',title:'Old question'}],counts:{needs:5}}
assert.equal(badgeState(stale,true).label,'Idle')
assert.equal(badgeState(stale,true).attention,false)
const board = {sessions:[{status:'working',title:'Current'},{status:'starting',title:'New'}, {status:'reply', title:'Finished task', activity_at:100, age_s:120}]}
assert.equal(badgeState(board,true).label,'2 running')
board.sessions[2].unread = true
assert.equal(badgeState(board,true).label,'2 running · 1 finished')
assert.equal(badgeState({sessions:[{status:'reply',unread:true},{status:'reply',unread:true}]},true).label,'2 finished')
assert.equal(badgeState({sessions:[{status:'reply',unread:false}]},true).label,'Idle')
assert.equal(badgeState({sessions:[{status:'reply',unread:true,age_s:5*3600}]},true).label,'Idle')
assert.equal(badgeState({sessions:[{status:'error',unread:true,age_s:60}]},true).label,'1 finished')
delete board.sessions[2].unread
assert.equal(badgeState(board,true).detail,'Last reply 2m ago · Finished task')
assert.equal(badgeState(board,false).label,'Reconnecting…')
board.sessions.push({title:'Approval task',approval_request_id:'req'})
assert.equal(badgeState(board,true).attention,true)
assert.equal(badgeState(board,true).label,'1 needs you · 2 running')
assert.match(badgeState(board,true,2).detail,/2 queued · 1 pending request/)
assert.equal(badgeState({sessions:[{provider:'t3',status:'working'}],providers:{t3:'offline'}},true).running,0)
console.log('Badge: running counts, latest reply, real requests, offline and queue states passed')
