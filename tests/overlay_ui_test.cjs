const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { pathToFileURL } = require('node:url')
const output = process.env.UI_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'agent-board-ui-'))
fs.mkdirSync(output, {recursive:true})
let browser
;(async () => {
 browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']})
 const page = await browser.newPage({ viewport: {width:760,height:640} })
 const errors=[]
 page.on('pageerror', error=>errors.push(error.message))
 await page.addInitScript(() => {
  window.handlers={}; window.actions=[]; window.commands=[]
  window.wow={
   onRoster: f=>handlers.roster=f, onLive:f=>handlers.live=f, onTheme:f=>handlers.theme=f,
   onMode:f=>handlers.mode=f,onFocus:f=>handlers.focus=f,
   action:async action=>{actions.push(action);return {ok:true}},
   command: cmd=>commands.push(cmd),chooseDirectory:async()=>({ok:false})
  }
 })
 await page.goto(pathToFileURL(path.join(__dirname, '..', 'overlay', 'index.html')).href)
 await page.evaluate(() => {
  window.testBoard={sessions:[
   {id:'one',provider:'t3',title:'Running task',status:'working',capabilities:['reply','stop'],conversation:[{role:'user',text:'start'}]},
   {id:'two',provider:'hermes',title:'Completed task',capabilities:['reply'],status:'reply',activity_at:20,age_s:120,conversation:[{role:'agent',text:'done'}]},
   {id:'old',provider:'hermes',title:'Stale question',status:'needs',conversation:[]}
  ],providers:{t3:'ok',hermes:'ok'},counts:{needs:5}}
  handlers.roster(testBoard);handlers.live({connected:true});handlers.mode({mode:'board'})
 })
 await page.locator('#composer-input').fill('queued text')
 await page.locator('#composer-input').press('Enter')
 assert.equal(await page.evaluate(()=>actions.length),0)
 await page.getByRole('button',{name:'Send now',exact:true}).first().click()
 assert.equal(await page.evaluate(()=>actions.length),1)
 assert.equal(await page.evaluate(()=>actions[0].delivery),'immediate')
 await page.locator('#composer-input').fill('draft one')
 await page.getByRole('option').filter({hasText:'Completed task'}).click()
 assert.equal(await page.locator('#composer-input').inputValue(),'')
 await page.locator('#composer-input').fill('draft two')
 await page.getByRole('option').filter({hasText:'Running task'}).click()
 assert.equal(await page.locator('#composer-input').inputValue(),'draft one')
 await page.evaluate(()=>{testBoard.sessions[0].user_input_request_id='req';testBoard.sessions[0].user_input_questions=[{id:'q1',question:'First?',options:[{label:'Yes'}]},{id:'q2',question:'Second?'}];handlers.roster(testBoard)})
 await page.getByRole('button',{name:'Yes',exact:true}).click()
 await page.getByRole('textbox',{name:'Second?'}).fill('two')
 await page.getByRole('button',{name:'Send answers',exact:true}).click()
 assert.deepEqual(await page.evaluate(()=>actions.at(-1).answers),{q1:'Yes',q2:'two'})
 await page.evaluate(()=>{delete testBoard.sessions[0].user_input_request_id;delete testBoard.sessions[0].user_input_questions;handlers.roster(testBoard)})
 await page.locator('#composer-input').press('Escape')
 assert.equal(await page.evaluate(()=>commands.at(-1)),'badge')
 await page.evaluate(()=>handlers.mode({mode:'badge'}))
 assert.equal(await page.locator('.badge-text').textContent(),'1 running')
 assert.match(await page.locator('#badge').getAttribute('title'),/Completed task/)
 await page.setViewportSize({width:150,height:26})
 await page.locator('#badge').screenshot({path:path.join(output,'badge.png'),omitBackground:true})
 await page.setViewportSize({width:760,height:640})
 await page.evaluate(()=>handlers.mode({mode:'board'}))
 await page.screenshot({path:path.join(output,'board.png')})
 // Grouping: blocked on you, then running, then unread, then seen.
  assert.deepEqual(await page.locator('.group-heading > span:first-child').allTextContents(),['Running','Just finished'])
 // Only a real request is "Needs you"; stale news falls to Earlier.
 await page.evaluate(()=>{testBoard.sessions[2].approval_request_id='req';testBoard.sessions[1].age_s=5*3600;handlers.roster(testBoard)})
 assert.deepEqual(await page.locator('.group-heading > span:first-child').allTextContents(),['Needs you','Running','Earlier'])
 await page.evaluate(()=>{delete testBoard.sessions[2].approval_request_id;testBoard.sessions[1].age_s=120;handlers.roster(testBoard)})
 // A streaming reply grows in place: same node, new text, nothing rebuilt.
 await page.getByRole('option').filter({hasText:'Running task'}).click()
 await page.evaluate(()=>{testBoard.sessions[0].conversation=[{id:'m1',role:'user',text:'start'},{id:'m2',role:'agent',text:'Hel'}];handlers.roster(testBoard)})
 await page.evaluate(()=>{window.streamNode=document.querySelector('.message.agent')})
 await page.evaluate(()=>{testBoard.sessions[0].conversation[1].text='Hello, streaming';handlers.roster(testBoard)})
 assert.equal(await page.evaluate(()=>document.querySelector('.message.agent')===window.streamNode),true)
 assert.equal(await page.locator('.message.agent .message-body').textContent(),'Hello, streaming')
 assert.equal(await page.locator('.message-working').count(),1)
 // Consecutive tool calls fold into one expandable line between messages.
 await page.evaluate(()=>{testBoard.sessions[0].conversation.splice(1,0,{id:'t1',role:'tool',text:'Ran `npm test`'},{id:'t2',role:'tool',text:'Edited overlay/main.js'});handlers.roster(testBoard)})
 assert.equal(await page.locator('.message.steps').count(),1)
 assert.equal(await page.locator('.message.steps .steps-count').textContent(),'2 steps')
 assert.equal(await page.locator('.message.steps .steps-last').textContent(),'Edited overlay/main.js')
 assert.equal(await page.locator('.message.steps li code').first().textContent(),'npm test')
 // Done actions follow the provider: settle in T3, archive in Hermes, nothing mid-turn.
 await page.evaluate(()=>{testBoard.sessions[0].status='working';testBoard.sessions[0].capabilities=['reply','settle'];testBoard.sessions[1].capabilities=['reply','archive'];handlers.roster(testBoard)})
 assert.equal(await page.locator('#done-button').isHidden(),true)
 await page.evaluate(()=>{testBoard.sessions[0].status='finished';handlers.roster(testBoard)})
 assert.equal(await page.locator('#done-button').textContent(),'Settle')
 await page.evaluate(()=>{testBoard.sessions[0].settled=true;handlers.roster(testBoard)})
 assert.equal(await page.locator('#done-button').textContent(),'Unsettle')
 await page.getByRole('option').filter({hasText:'Completed task'}).click()
 assert.equal(await page.locator('#done-button').textContent(),'Archive')
 await page.locator('#done-button').click()
 assert.equal(await page.evaluate(()=>actions.at(-1).kind),'archive')
 // Right-click menu: provider-specific items, a two-step T3 archive, keyboard.
 await page.evaluate(()=>{testBoard.sessions[0].capabilities=['reply','settle','archive','mark_unread','mark_read'];testBoard.sessions[0].settled=false;handlers.roster(testBoard)})
 await page.getByRole('option').filter({hasText:'Running task'}).click({button:'right'})
 const labels = await page.locator('.row-menu-item span').allTextContents()
 assert.deepEqual(labels,['Open','Mark unread','Settle','Copy title','Copy session ID','Archive in T3 Code'])
 const before = await page.evaluate(()=>actions.length)
 await page.locator('.row-menu-item.danger').click()
 assert.equal(await page.locator('#row-menu').isVisible(),true)
 assert.equal(await page.evaluate(()=>actions.length),before)
 await page.locator('.row-menu-item.danger').click()
 assert.equal(await page.locator('#row-menu').isHidden(),true)
 assert.equal(await page.evaluate(()=>actions.at(-1).kind),'archive')
 await page.getByRole('option').filter({hasText:'Running task'}).click({button:'right'})
 await page.keyboard.press('ArrowDown')
 await page.keyboard.press('Enter')
 assert.equal(await page.evaluate(()=>actions.at(-1).kind),'mark_unread')
 await page.getByRole('option').filter({hasText:'Running task'}).click({button:'right'})
 await page.keyboard.press('Escape')
 assert.equal(await page.locator('#row-menu').isHidden(),true)
 assert.equal(await page.evaluate(()=>commands.at(-1)==='badge'),false)

 // Streaming must keep completed Markdown blocks and horizontally scrolled code.
 await page.getByRole('option').filter({hasText:'Running task'}).click()
 await page.evaluate(()=>{
  const s=testBoard.sessions[0];s.status='working';s.capabilities=['reply','stop'];
  s.conversation=[{id:'stream',role:'agent',streaming:true,text:'Stable paragraph.\n\n```js\n'+ 'const value = 123; '.repeat(30)}];
  handlers.roster(testBoard)
  window.stableParagraph=document.querySelector('.message-body p')
  window.codeScroller=document.querySelector('.md-code pre')
  window.codeScroller.scrollLeft=100
  window.workingNode=document.querySelector('.message-working')
  document.querySelector('.md-copy').focus()
  s.conversation[0].text+='\nconst next = true;';handlers.roster(testBoard)
 })
 assert.equal(await page.evaluate(()=>document.querySelector('.message-body p')===stableParagraph),true)
 assert.equal(await page.evaluate(()=>document.querySelector('.md-code pre')===codeScroller && codeScroller.scrollLeft===100),true)
 assert.equal(await page.evaluate(()=>document.activeElement.matches('.md-copy')),true)
 assert.equal(await page.evaluate(()=>document.querySelector('.message-working')===workingNode),true)
 // Tool rows can change text without changing identity.
 await page.evaluate(()=>{testBoard.sessions[0].conversation.unshift({id:'tool',role:'tool',text:'Running tests'});handlers.roster(testBoard)})
 await page.locator('.steps summary').click()
 await page.evaluate(()=>{testBoard.sessions[0].conversation[0].text='Tests passed';handlers.roster(testBoard)})
 assert.equal(await page.locator('.steps-list li').textContent(),'Tests passed')
 assert.equal(await page.locator('.steps').getAttribute('open'),'')
 // Same-status unread and snippet changes invalidate the sidebar.
 await page.evaluate(()=>{const s=testBoard.sessions[1];s.status='reply';s.age_s=2000;s.unread=true;s.snippet='First';handlers.roster(testBoard)})
 assert.match(await page.locator('.row[data-id="two"]').textContent(),/Unread reply.*First/)
 await page.evaluate(()=>{testBoard.sessions[1].unread=false;testBoard.sessions[1].snippet='Second';handlers.roster(testBoard)})
 assert.match(await page.locator('.row[data-id="two"]').textContent(),/Finished/)
 assert.equal(await page.locator('.row[data-id="two"]').evaluate(el=>el.classList.contains('earlier')),true)
 // Follow the bottom, but preserve the reading anchor when the 60-message window slides.
 await page.evaluate(()=>{
  testBoard.sessions[0].conversation=Array.from({length:60},(_,i)=>({id:'long-'+i,role:i%2?'agent':'user',text:('Message '+i+' with enough text to wrap. ').repeat(8)}))
  handlers.roster(testBoard)
 })
 await page.waitForTimeout(100)
 await page.evaluate(()=>{document.querySelector('#conversation').scrollTop=1500})
 await page.waitForTimeout(100)
 await page.evaluate(()=>{
  const box=document.querySelector('#conversation');const top=box.getBoundingClientRect().top
  window.anchorNode=[...box.querySelectorAll('.message')].find(n=>n.getBoundingClientRect().bottom>top)
  window.anchorTop=anchorNode.getBoundingClientRect().top
  testBoard.sessions[0].conversation.shift()
  testBoard.sessions[0].conversation.push({id:'new-bottom',role:'agent',text:'New response\n'.repeat(30)})
  handlers.roster(testBoard)
 })
 await page.waitForTimeout(100)
 assert.equal(await page.evaluate(()=>anchorNode.isConnected),true)
 assert.ok(await page.evaluate(()=>Math.abs(anchorNode.getBoundingClientRect().top-anchorTop)<2))
 assert.equal(await page.locator('#jump-latest').isVisible(),true)
 const readingTop=await page.locator('#conversation').evaluate(el=>el.scrollTop)
 await page.locator('#search-input').fill('no match anywhere')
 assert.equal(await page.locator('#empty').textContent(),'No sessions match your search.')
 assert.equal(await page.locator('#conversation').evaluate(el=>el.scrollTop),readingTop)
 await page.locator('#search-input').fill('')
 // Reconnect keeps drafts, disables actions and removes the misleading working animation.
 await page.locator('#composer-input').fill('Keep this draft')
 await page.evaluate(()=>handlers.live({connected:false}))
 assert.equal(await page.locator('#stop-button').isDisabled(),true)
 assert.equal(await page.locator('#connection-notice').isVisible(),true)
 assert.equal(await page.locator('.message-working').count(),0)
 await page.locator('#composer-input').press('Control+Enter')
 assert.equal(await page.locator('#composer-input').inputValue(),'Keep this draft')
 await page.evaluate(()=>handlers.live({connected:true}))
 assert.equal(await page.locator('#stop-button').isEnabled(),true)
 await page.evaluate(()=>{testBoard.providers.t3='offline';handlers.roster(testBoard)})
 assert.equal(await page.locator('#stop-button').isDisabled(),true)
 assert.match(await page.locator('#connection-notice').textContent(),/T3 Code is unavailable/)
 await page.evaluate(()=>{testBoard.providers.t3='ok';testBoard.projects=[{id:'p1',title:'Uploader'},{id:'p2',title:'Docs'}];handlers.roster(testBoard)})
 // Modal focus, project keyboard navigation and repeated Ctrl+Enter while starting.
 await page.locator('#new-session-button').click()
 await page.locator('#new-session-close').focus()
 await page.keyboard.press('Shift+Tab')
 assert.equal(await page.locator('#new-session-start').evaluate(el=>el===document.activeElement),true)
 await page.keyboard.press('Tab')
 assert.equal(await page.locator('#new-session-close').evaluate(el=>el===document.activeElement),true)
 await page.locator('#project-trigger').focus()
 await page.keyboard.press('ArrowDown')
 await page.keyboard.press('ArrowDown')
 await page.keyboard.press('Enter')
 assert.equal(await page.locator('#project-trigger-label').textContent(),'Docs')
 await page.locator('#new-session-prompt').fill('Test a new session')
 await page.evaluate(()=>{window.wow.action=action=>{actions.push(action);return new Promise(resolve=>window.resolveNew=resolve)}})
 const countBefore=await page.evaluate(()=>actions.length)
 await page.locator('#new-session-prompt').press('Control+Enter')
 await page.locator('#new-session-prompt').press('Control+Enter')
 assert.equal(await page.evaluate(()=>actions.length),countBefore+1)
 assert.equal(await page.locator('#detail-title').textContent(),'Running task')
 await page.evaluate(()=>resolveNew({ok:false,error:'Fixture failure'}))
 assert.equal(await page.locator('#new-session').isVisible(),true)
 assert.equal(await page.locator('#new-session-prompt').inputValue(),'Test a new session')
 await page.keyboard.press('Escape')
 assert.equal(await page.locator('#new-session-button').evaluate(el=>el===document.activeElement),true)
 // Geometry at supported minimum, default and larger sizes, in both themes.
 for (const [width,height] of [[640,460],[980,720],[1280,900]]) {
  await page.setViewportSize({width,height})
  await page.locator('#composer-input').fill('')
  for (const appearance of ['dark','light']) {
   await page.evaluate(appearance=>handlers.theme({appearance,variables:appearance==='light'?{
    '--om-background':'#faf9f5','--om-sidebar':'#f0eee6','--om-darker':'#e5e1d5','--om-text':'#252a27',
    '--om-muted':'#5c625d','--om-focus':'#295f49','--om-accent':'#357657','--om-selected':'#d2e3d5'
   }:{'--om-background':'#111c18','--om-sidebar':'#0c1512','--om-darker':'#090f0d','--om-text':'#c1c497',
    '--om-muted':'#53685b','--om-focus':'#c1c497','--om-accent':'#509475','--om-selected':'#32473b'}}),appearance)
   const geometry=await page.evaluate(()=>{
    const composer=document.querySelector('#composer-input').getBoundingClientRect()
    const transcript=document.querySelector('#conversation').getBoundingClientRect()
    return {composerBottom:composer.bottom,transcriptHeight:transcript.height,overflow:document.querySelector('#board').scrollWidth>innerWidth}
   })
   assert.ok(geometry.composerBottom<=height,JSON.stringify(geometry))
   assert.ok(geometry.transcriptHeight>100,JSON.stringify(geometry))
   assert.equal(geometry.overflow,false)
   await page.screenshot({path:path.join(output,`board-${width}-${appearance}.png`)})
  }
 }


 // Requests, queued text and a long prompt must leave controls on-screen at the minimum size.
 await page.setViewportSize({width:640,height:460})
 await page.evaluate(()=>{
  testBoard.sessions[0].user_input_request_id='layout-request'
  testBoard.sessions[0].user_input_summary='Choose the deployment strategy before continuing.'
  testBoard.sessions[0].user_input_questions=[{id:'layout-1',question:'Which environment?',options:[{label:'Staging'},{label:'Production'}]},{id:'layout-2',question:'What should we verify?'}]
  handlers.roster(testBoard)
 })
 await page.getByRole('button',{name:'Staging',exact:true}).click()
 assert.equal(await page.getByRole('button',{name:'Staging',exact:true}).getAttribute('aria-pressed'),'true')
 const requestLayout=await page.evaluate(()=>({
  answerBottom:document.querySelector('#answer-button').getBoundingClientRect().bottom,
  composerBottom:document.querySelector('#composer-input').getBoundingClientRect().bottom,
  transcript:document.querySelector('#conversation').clientHeight
 }))
 assert.ok(requestLayout.answerBottom<=460 && requestLayout.composerBottom<=460,JSON.stringify(requestLayout))
 assert.ok(requestLayout.transcript>80,JSON.stringify(requestLayout))
 await page.screenshot({path:path.join(output,'questions-640-light.png')})
 await page.locator('#new-session-button').click()
 await page.screenshot({path:path.join(output,'new-session-640-light.png')})
 await page.keyboard.press('Escape')
 await page.emulateMedia({reducedMotion:'reduce'})
 assert.equal(await page.locator('.row .dot.working').first().evaluate(el=>getComputedStyle(el).animationName),'none')
 assert.deepEqual(errors,[])
 await browser.close()
 console.log('Screenshots:',output)
 console.log('UI passed: queue/send-now, drafts, requests, badge, grouping, stable streaming/code scroll, transcript anchors, tool updates, offline controls, modal keyboard/duplicate-submit, dark/light geometry, reduced motion; no renderer errors')
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{await browser?.close()})
