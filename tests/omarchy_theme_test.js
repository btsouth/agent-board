'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { loadOmarchyTheme, themeSignature } = require('../overlay/omarchy_theme')

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-board-theme-'))
const current = path.join(root, 'omarchy', 'current')
const theme = path.join(current, 'theme')
fs.mkdirSync(theme, { recursive: true })
fs.writeFileSync(path.join(current, 'theme.name'), 'osaka-jade\n')
fs.writeFileSync(path.join(theme, 'colors.toml'), `
mode = "dark"
background = "#111c18"
dark_background = "#0c1512"
darker_background = "#090f0d"
lighter_background = "#23372b"
accent = "#509475"
selection = "#32473B"
muted = "#53685B"
foreground = "#C1C497"
red = "#FF5345"
yellow = "#E5C736"
green = "#549e6a"
cyan = "#2DD5B7"
blue = "#509475"
magenta = "#D2689C"
`)
fs.writeFileSync(path.join(theme, 't3code.json'), JSON.stringify({
  name: 'Omarchy',
  appearance: 'dark',
  canvas: '#111c18',
  accent: '#509475',
  colors: {
    text: '#C1C497',
    border: '#53685B',
    sidebar: '#0c1512',
    sidebarRowHover: '#17201a',
    sidebarRowSelected: '#32473B',
    error: '#e97b62',
    warning: '#70a56a',
    focus: '#509475',
  },
}))

const loaded = loadOmarchyTheme(root)
assert.equal(loaded.name, 'osaka-jade')
assert.equal(loaded.appearance, 'dark')
assert.equal(loaded.variables['--om-background'], '#111c18')
assert.equal(loaded.variables['--om-sidebar'], '#0c1512')
assert.equal(loaded.variables['--om-accent'], '#509475')
assert.equal(loaded.variables['--om-success'], '#549e6a')

const before = themeSignature(loaded)
fs.writeFileSync(path.join(theme, 't3code.json'), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(theme, 't3code.json'))), accent: '#ff0000' }))
const changed = loadOmarchyTheme(root)
assert.notEqual(themeSignature(changed), before)
assert.equal(changed.variables['--om-accent'], '#ff0000')

fs.rmSync(root, { recursive: true, force: true })
console.log('OMARCHY THEME OK')
