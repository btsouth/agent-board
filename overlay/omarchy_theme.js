'use strict'

const fs = require('node:fs')
const path = require('node:path')

const FALLBACK_THEME = {
  name: 'Omarchy',
  appearance: 'dark',
  variables: {
    '--om-background': '#111c18',
    '--om-sidebar': '#0c1512',
    '--om-darker': '#090f0d',
    '--om-lighter': '#23372b',
    '--om-text': '#c1c497',
    '--om-muted': '#53685b',
    '--om-accent': '#509475',
    '--om-focus': '#c1c497',
    '--om-selected': '#32473b',
    '--om-hover': '#17201a',
    '--om-border': '#53685b',
    '--om-warning': '#e5c736',
    '--om-info': '#acd4cf',
    '--om-success': '#63b07a',
    '--om-waiting': '#75bbb3',
    '--om-error': '#e97b62',
    '--om-font': '"Noto Sans", sans-serif',
  },
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8')
  } catch {
    return ''
  }
}

function readJson(filePath) {
  try {
    return JSON.parse(readText(filePath))
  } catch {
    return null
  }
}

function readSimpleToml(text) {
  const values = {}
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*["']([^"']+)["']\s*(?:#.*)?$/)
    if (match) values[match[1]] = match[2]
  }
  return values
}

function loadOmarchyTheme(stateHome = process.env.XDG_STATE_HOME || path.join(process.env.HOME || '', '.local', 'state')) {
  const current = path.join(stateHome, 'omarchy', 'current')
  const themeDir = path.join(current, 'theme')
  const t3 = readJson(path.join(themeDir, 't3code.json')) || {}
  const colors = readSimpleToml(readText(path.join(themeDir, 'colors.toml')))
  const c = t3.colors || {}
  const variables = {
    '--om-background': t3.canvas || colors.background || FALLBACK_THEME.variables['--om-background'],
    '--om-sidebar': c.sidebar || colors.dark_background || t3.canvas || FALLBACK_THEME.variables['--om-sidebar'],
    '--om-darker': colors.darker_background || c.sidebar || FALLBACK_THEME.variables['--om-darker'],
    '--om-lighter': colors.lighter_background || c.sidebarRowHover || FALLBACK_THEME.variables['--om-lighter'],
    '--om-text': c.text || colors.foreground || FALLBACK_THEME.variables['--om-text'],
    '--om-muted': colors.muted || c.border || FALLBACK_THEME.variables['--om-muted'],
    '--om-accent': t3.accent || colors.accent || FALLBACK_THEME.variables['--om-accent'],
    '--om-focus': c.focus || colors.accent || FALLBACK_THEME.variables['--om-focus'],
    '--om-selected': c.sidebarRowSelected || colors.selection || FALLBACK_THEME.variables['--om-selected'],
    '--om-hover': c.sidebarRowHover || colors.dark_background || FALLBACK_THEME.variables['--om-hover'],
    '--om-border': c.border || colors.muted || FALLBACK_THEME.variables['--om-border'],
    '--om-warning': c.warning || colors.yellow || FALLBACK_THEME.variables['--om-warning'],
    '--om-info': colors.cyan || colors.blue || c.focus || FALLBACK_THEME.variables['--om-info'],
    '--om-success': colors.green || c.focus || FALLBACK_THEME.variables['--om-success'],
    '--om-waiting': colors.magenta || colors.cyan || c.focus || FALLBACK_THEME.variables['--om-waiting'],
    '--om-error': c.error || colors.red || FALLBACK_THEME.variables['--om-error'],
    '--om-font': FALLBACK_THEME.variables['--om-font'],
  }
  return {
    name: readText(path.join(current, 'theme.name')).trim() || t3.name || 'Omarchy',
    appearance: t3.appearance || (colors.mode === 'light' ? 'light' : 'dark'),
    variables,
  }
}

function themeSignature(theme) {
  return JSON.stringify(theme)
}

module.exports = { FALLBACK_THEME, loadOmarchyTheme, readSimpleToml, themeSignature }
