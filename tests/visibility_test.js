'use strict'
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Visibility } = require('../overlay/visibility')
let now = 100
const v = new Visibility('automatic', () => now)
assert.equal(v.visible, false)
v.update({ running: true }); assert.equal(v.visible, true)
v.set('paused'); assert.equal(v.visible, false)
v.update({ running: false }); assert.equal(v.policy, 'automatic')
v.update({ running: true }); assert.equal(v.visible, true)
now += 16000; assert.equal(v.visible, false)
v.set('manual'); assert.equal(v.visible, true)
v.update({ running: false }); assert.equal(v.visible, true)

// Execute the real placement transition with a compositor request held open.
const source = fs.readFileSync(require.resolve('../overlay/main.js'), 'utf8')
const apply = source.slice(source.indexOf('function applyMode('), source.indexOf('async function refreshRoster('))
const sync = source.slice(source.indexOf('function syncGameVisibility('), source.indexOf('async function pinWindow('))
const context = vm.createContext({ assert, Visibility, setTimeout: f => { queueMicrotask(f); return 1 }, clearTimeout: () => {} })
vm.runInContext(`
let hiddenForGame=false, mode='badge', modeEpoch=0, modeTransition=Promise.resolve(), transitioning=false, displayedMode=null;
const visibility=new Visibility('automatic'); visibility.update({running:true});
const GEOMETRY={badge:{}}, HEADLESS=false, modeFrames=new Map();
const visual={visible:false,opacity:0};
const win={isDestroyed:()=>false,isFocused:()=>false,setFocusable:()=>{},setOpacity:v=>visual.opacity=v,setIgnoreMouseEvents:()=>{},hide:()=>visual.visible=false,setSize:()=>{},showInactive:()=>visual.visible=true,focus:()=>{}};
const geometryFor=()=>({width:150,height:26}),send=()=>{},samplePosition=async()=>{},nativePosition=async()=>null,rememberPosition=()=>{},log=()=>{};
let finishPin; let delayed=true; const pinWindow=()=>delayed ? new Promise(resolve=>finishPin=resolve) : Promise.resolve();
${sync}\n${apply}
globalThis.run=async()=>{
 const pending=applyMode('badge');
 await new Promise(resolve=>setTimeout(resolve,0));
 visibility.update({running:false}); syncGameVisibility();
 finishPin(); await pending;
 assert.equal(hiddenForGame,true); assert.equal(visual.visible,false);
 delayed=false; visibility.update({running:true}); syncGameVisibility(); await modeTransition;
 assert.equal(hiddenForGame,false); assert.equal(visual.visible,true); assert.equal(visual.opacity,1);
 visibility.set('paused'); syncGameVisibility(); assert.equal(visual.visible,false);
 manualVisibility(); await modeTransition; assert.equal(visual.visible,true);
};`, context)
context.run().then(() => console.log('Visibility: automatic, manual, pause, stale bridge, exit during pin, rapid restart passed'))
  .catch(error => { console.error(error); process.exitCode = 1 })
