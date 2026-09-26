'use strict'
// Agent replies are Markdown. This renders the subset agents actually write
// (fences, headings, lists, quotes, tables, inline code, emphasis, links) by
// building DOM nodes directly: text never reaches innerHTML, so a reply cannot
// inject markup into the overlay.

const FENCE = /^\s*(```+|~~~+)\s*([\w+#.-]*)\s*$/
const HEADING = /^(#{1,6})\s+(.*)$/
const BULLET = /^(\s*)([-*+])\s+(.*)$/
const ORDERED = /^(\s*)(\d+)[.)]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

function parseBlocks(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    const fence = line.match(FENCE)
    if (fence) {
      const body = []
      index += 1
      // An unclosed fence is normal mid-stream: everything after it is code.
      while (index < lines.length && !lines[index].trim().startsWith(fence[1])) body.push(lines[index++])
      blocks.push({ type: 'code', lang: fence[2], text: body.join('\n'), open: index >= lines.length })
      index += 1
      continue
    }
    if (!line.trim()) { index += 1; continue }
    const heading = line.match(HEADING)
    if (heading) { blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] }); index += 1; continue }
    if (RULE.test(line)) { blocks.push({ type: 'rule' }); index += 1; continue }
    if (line.includes('|') && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
      const rows = [splitRow(line)]
      index += 2
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) rows.push(splitRow(lines[index++]))
      blocks.push({ type: 'table', rows })
      continue
    }
    if (QUOTE.test(line)) {
      const body = []
      while (index < lines.length && QUOTE.test(lines[index])) body.push(lines[index++].match(QUOTE)[1])
      blocks.push({ type: 'quote', blocks: parseBlocks(body.join('\n')) })
      continue
    }
    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = !BULLET.test(line)
      const pattern = ordered ? ORDERED : BULLET
      const items = []
      while (index < lines.length) {
        const current = lines[index]
        const match = current.match(pattern)
        if (match) { items.push({ text: match[3], start: ordered ? Number(match[2]) : null }); index += 1; continue }
        // Indented continuation lines belong to the item above.
        if (current.trim() && /^\s{2,}/.test(current) && items.length && !FENCE.test(current)) {
          items[items.length - 1].text += `\n${current.trim()}`
          index += 1
          continue
        }
        break
      }
      blocks.push({ type: 'list', ordered, start: items[0]?.start || 1, items })
      continue
    }
    const paragraph = []
    while (index < lines.length && lines[index].trim() && !FENCE.test(lines[index]) && !HEADING.test(lines[index]) &&
      !BULLET.test(lines[index]) && !ORDERED.test(lines[index]) && !QUOTE.test(lines[index]) && !RULE.test(lines[index])) {
      paragraph.push(lines[index++])
    }
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') })
  }
  return blocks
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
}

// Inline: code spans first (their content is literal), then links, bold, italic.
const INLINE = /(`+)([\s\S]*?[^`])\1(?!`)|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|(?<![\w*])\*([^*\n]+?)\*(?![\w*])|(?<![\w_])_([^_\n]+?)_(?![\w_])/g

function appendInline(parent, text, doc) {
  const source = String(text || '')
  let last = 0
  for (const match of source.matchAll(INLINE)) {
    if (match.index > last) appendText(parent, source.slice(last, match.index), doc)
    last = match.index + match[0].length
    if (match[1]) {
      const code = doc.createElement('code')
      code.textContent = match[2].replace(/^ (.*) $/, '$1')
      parent.append(code)
    } else if (match[3]) {
      parent.append(link(match[3], match[4], doc))
    } else if (match[5]) {
      parent.append(link(match[5], match[5], doc))
    } else if (match[6] || match[7]) {
      const strong = doc.createElement('strong')
      appendInline(strong, match[6] || match[7], doc)
      parent.append(strong)
    } else {
      const em = doc.createElement('em')
      appendInline(em, match[8] || match[9], doc)
      parent.append(em)
    }
  }
  if (last < source.length) appendText(parent, source.slice(last), doc)
}

function appendText(parent, text, doc) {
  const parts = text.split('\n')
  parts.forEach((part, index) => {
    if (index) parent.append(doc.createElement('br'))
    if (part) parent.append(doc.createTextNode(part))
  })
}

function link(label, href, doc) {
  const anchor = doc.createElement('a')
  anchor.textContent = label
  anchor.href = href
  anchor.dataset.external = href
  anchor.title = href
  return anchor
}

function renderBlocks(parent, blocks, doc, options) {
  for (const block of blocks) {
    if (block.type === 'code') {
      const wrap = doc.createElement('div')
      wrap.className = 'md-code'
      wrap.codeText = block.text
      const head = doc.createElement('div')
      head.className = 'md-code-head'
      const lang = doc.createElement('span')
      lang.textContent = block.lang || 'code'
      head.append(lang)
      if (options.onCopy) {
        const copy = doc.createElement('button')
        copy.type = 'button'
        copy.className = 'md-copy'
        copy.textContent = 'Copy'
        copy.addEventListener('click', () => options.onCopy(wrap.codeText, copy))
        head.append(copy)
      }
      const pre = doc.createElement('pre')
      const code = doc.createElement('code')
      code.textContent = block.text
      pre.append(code)
      wrap.append(head, pre)
      parent.append(wrap)
    } else if (block.type === 'heading') {
      const heading = doc.createElement(`h${Math.min(6, block.level + 2)}`)
      heading.className = 'md-heading'
      appendInline(heading, block.text, doc)
      parent.append(heading)
    } else if (block.type === 'rule') {
      parent.append(doc.createElement('hr'))
    } else if (block.type === 'quote') {
      const quote = doc.createElement('blockquote')
      renderBlocks(quote, block.blocks, doc, options)
      parent.append(quote)
    } else if (block.type === 'list') {
      const list = doc.createElement(block.ordered ? 'ol' : 'ul')
      if (block.ordered && block.start !== 1) list.start = block.start
      for (const item of block.items) {
        const li = doc.createElement('li')
        appendInline(li, item.text, doc)
        list.append(li)
      }
      parent.append(list)
    } else if (block.type === 'table') {
      const scroller = doc.createElement('div')
      scroller.className = 'md-table'
      const table = doc.createElement('table')
      block.rows.forEach((cells, rowIndex) => {
        const tr = doc.createElement('tr')
        for (const cell of cells) {
          const td = doc.createElement(rowIndex === 0 ? 'th' : 'td')
          appendInline(td, cell, doc)
          tr.append(td)
        }
        table.append(tr)
      })
      scroller.append(table)
      parent.append(scroller)
    } else {
      const paragraph = doc.createElement('p')
      appendInline(paragraph, block.text, doc)
      parent.append(paragraph)
    }
  }
}

function renderMarkdown(parent, text, options = {}) {
  const doc = parent.ownerDocument
  const blocks = parseBlocks(text)
  const existing = [...parent.children]
  blocks.forEach((block, index) => {
    const signature = JSON.stringify(block)
    let node = existing[index]
    if (node?.markdownSignature === signature) return
    // Preserve completed blocks, code scroll positions and copy-button focus
    // while the final block grows. Copy always reads the latest code.
    if (node?.classList.contains('md-code') && block.type === 'code' && node.codeLanguage === block.lang) {
      node.codeText = block.text
      node.querySelector('code').textContent = block.text
    } else {
      const fragment = doc.createDocumentFragment()
      renderBlocks(fragment, [block], doc, options)
      const fresh = fragment.firstChild
      if (node) node.replaceWith(fresh)
      else parent.append(fresh)
      node = fresh
    }
    node.markdownSignature = signature
    node.codeLanguage = block.lang
  })
  for (const node of existing.slice(blocks.length)) node.remove()
}

function renderInline(parent, text) {
  parent.replaceChildren()
  appendInline(parent, String(text || '').replace(/\n/g, ' '), parent.ownerDocument)
}

if (typeof module !== 'undefined') module.exports = { parseBlocks, renderMarkdown }
else { window.renderMarkdown = renderMarkdown; window.renderInline = renderInline }
