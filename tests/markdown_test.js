'use strict'
const assert = require('node:assert/strict')
const { parseBlocks } = require('../overlay/markdown')

const types = text => parseBlocks(text).map(block => block.type)
assert.deepEqual(types('# Title\nbody'), ['heading', 'paragraph'])
assert.deepEqual(types('- one\n- two\n\n1. a\n2. b'), ['list', 'list'])
assert.equal(parseBlocks('3. c\n4. d')[0].start, 3)
assert.equal(parseBlocks('- item\n  continued')[0].items[0].text, 'item\ncontinued')
assert.deepEqual(types('| a | b |\n|---|---|\n| 1 | 2 |'), ['table'])
assert.deepEqual(parseBlocks('| a | b |\n|---|---|\n| 1 | 2 |')[0].rows, [['a', 'b'], ['1', '2']])
assert.deepEqual(types('> quoted\n> more'), ['quote'])
assert.deepEqual(types('---'), ['rule'])

// A fence the agent has not closed yet is still code, not prose.
const open = parseBlocks('Before\n```js\nconst x = 1')
assert.deepEqual(open.map(block => block.type), ['paragraph', 'code'])
assert.equal(open[1].lang, 'js')
assert.equal(open[1].text, 'const x = 1')
assert.equal(open[1].open, true)

// Markup inside a fence stays literal text.
assert.equal(parseBlocks('```\n<img src=x onerror=alert(1)>\n# not a heading\n```')[0].text, '<img src=x onerror=alert(1)>\n# not a heading')
console.log('Markdown: blocks, lists, tables, quotes, streaming fences, literal code passed')
