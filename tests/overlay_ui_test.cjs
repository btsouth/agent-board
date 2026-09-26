const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { pathToFileURL } = require('node:url')
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-board-ui-'))
;(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']})
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
   {id:'two',provider:'hermes',title:'Completed task',status:'reply',activity_at:20,age_s:120,conversation:[{role:'agent',text:'done'}]},
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
 assert.equal(await page.locator('.badge-text').textContent(),'1 agent running')
 assert.match(await page.locator('.badge-detail').textContent(),/Completed task/)
 await page.setViewportSize({width:286,height:46})
 await page.locator('#badge').screenshot({path:path.join(output,'badge.png'),omitBackground:true})
 await page.setViewportSize({width:760,height:640})
 await page.evaluate(()=>handlers.mode({mode:'board'}))
 await page.screenshot({path:path.join(output,'board.png')})
 assert.deepEqual(errors,[])
 await browser.close()
 console.log('Screenshots:',output)
 console.log('UI passed: queue/send-now, independent drafts, structured questions, Escape, badge, no renderer errors')
})().catch(error=>{console.error(error);process.exitCode=1})
