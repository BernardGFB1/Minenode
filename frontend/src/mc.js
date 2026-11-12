// Minecraft text helpers: § codes <-> rich HTML for the WYSIWYG MOTD editor, dirt background, obfuscation.
import { MC_COLORS } from './utils'

export const MC_FONT = "'MineNode Minecraft', 'Pixelify Sans', 'JetBrains Mono Variable', monospace"
export const DEFAULT_COLOR = '#AAAAAA' // server-list MOTD text is gray unless coloured
const OBF = 'mcobf'
const HEX = Object.fromEntries(MC_COLORS.map(([c, h]) => [c, h]))
const RGB = MC_COLORS.map(([c, h]) => [c, parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)])

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

/** § string → HTML with inline styles (what contenteditable + execCommand produce and understand). */
export function codesToEditorHtml(text) {
  let html = ''
  let st = { c: null, l: false, o: false, n: false, m: false, k: false }
  const parts = (text || '').replace(/\\n/g, '\n').split(/§(?=[0-9a-fk-or])/i)
  const push = (chunk) => {
    if (!chunk) return
    const css = []
    if (st.c) css.push(`color: ${HEX[st.c]}`)
    if (st.l) css.push('font-weight: bold')
    if (st.o) css.push('font-style: italic')
    const deco = [st.n && 'underline', st.m && 'line-through'].filter(Boolean)
    if (deco.length) css.push(`text-decoration: ${deco.join(' ')}`)
    if (st.k) css.push(`font-family: ${OBF}`)
    const body = chunk.split('\n').map(esc).join('<br>')
    html += css.length ? `<span style="${css.join('; ')}">${body}</span>` : body
  }
  push(parts[0])
  for (let i = 1; i < parts.length; i++) {
    const code = parts[i][0].toLowerCase()
    if (HEX[code]) st = { c: code, l: false, o: false, n: false, m: false, k: false }
    else if (code === 'r') st = { c: null, l: false, o: false, n: false, m: false, k: false }
    else if (code === 'l') st.l = true
    else if (code === 'o') st.o = true
    else if (code === 'n') st.n = true
    else if (code === 'm') st.m = true
    else if (code === 'k') st.k = true
    push(parts[i].slice(1))
  }
  return html
}

function nearestCode(cssColor) {
  const m = cssColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
  if (!m) return null
  const [r, g, b] = m.slice(1).map(Number)
  let best = null, bd = Infinity
  for (const [c, cr, cg, cb] of RGB) {
    const d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    if (d < bd) { bd = d; best = c }
  }
  return best === '7' ? null : best // gray == default
}

function styleOf(el, root) {
  const cs = getComputedStyle(el)
  let n = false, m = false
  for (let x = el; x && x !== root.parentElement; x = x.parentElement) {
    const d = getComputedStyle(x).textDecorationLine || ''
    if (d.includes('underline') || x.tagName === 'U') n = true
    if (d.includes('line-through') || x.tagName === 'S' || x.tagName === 'STRIKE') m = true
    if (x === root) break
  }
  const k = !!el.closest(`[style*="${OBF}"], font[face*="${OBF}"]`)
  return { c: nearestCode(cs.color), l: parseInt(cs.fontWeight, 10) >= 600, o: cs.fontStyle === 'italic', n, m, k }
}

/** Editor DOM → § string. */
export function serializeEditor(root) {
  let out = ''
  let cur = { c: null, l: false, o: false, n: false, m: false, k: false }
  const flags = ['l', 'o', 'n', 'm', 'k']
  const emit = (text, st) => {
    const lost = flags.some((f) => cur[f] && !st[f])
    if (lost || st.c !== cur.c) {
      out += st.c ? `§${st.c}` : '§r'
      flags.forEach((f) => st[f] && (out += `§${f}`))
    } else {
      flags.forEach((f) => st[f] && !cur[f] && (out += `§${f}`))
    }
    cur = { ...st }
    out += text
  }
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        const t = child.nodeValue.replace(/​/g, '')
        if (t) emit(t, styleOf(child.parentElement, root))
      } else if (child.nodeName === 'BR') {
        out += '\n'
      } else if (child.nodeType === 1) {
        const block = /^(DIV|P)$/.test(child.nodeName)
        if (block && out && !out.endsWith('\n')) out += '\n'
        walk(child)
      }
    }
  }
  walk(root)
  // A leading "§r" is meaningless; trailing newlines come from contenteditable placeholders.
  let res = out.replace(/^§r/, '')
  // drop trailing newlines and formatting codes that no longer style any text
  while (/(\n|§[0-9a-fk-or])$/i.test(res)) res = res.replace(/(\n|§[0-9a-fk-or])$/i, '')
  return res
}

// ---------------------------------------------------------------- dirt-style background (procedural, original)
let dirtUrl = null
export function dirtBackground() {
  if (dirtUrl) return dirtUrl
  const c = document.createElement('canvas')
  c.width = c.height = 16
  const ctx = c.getContext('2d')
  let seed = 7
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  const tones = [[38, 27, 20], [45, 32, 23], [31, 22, 16], [52, 38, 27], [27, 19, 14]]
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const t = tones[Math.floor(rnd() * tones.length)]
    ctx.fillStyle = `rgb(${t[0]},${t[1]},${t[2]})`
    ctx.fillRect(x, y, 1, 1)
  }
  dirtUrl = c.toDataURL()
  return dirtUrl
}

// ---------------------------------------------------------------- §k obfuscation animation
const POOL = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$%&*+=?@'
export function animateObfuscated(root) {
  const timer = setInterval(() => {
    root?.querySelectorAll('.mc-k').forEach((el) => {
      if (!el.dataset.len) el.dataset.len = el.textContent.length
      let s = ''
      for (let i = 0; i < +el.dataset.len; i++) s += POOL[Math.floor(Math.random() * POOL.length)]
      el.textContent = s
    })
  }, 70)
  return () => clearInterval(timer)
}

export const SPECIAL_CHARS = [
  '❤', '♥', '★', '☆', '✦', '✧', '✪', '✯', '☀', '☁', '☂', '❄', '☃', '⚡', '♫', '♪', '☠', '⚔', '⛏',
  '✔', '✖', '✚', '➜', '➤', '»', '«', '▶', '◀', '▲', '▼', '●', '○', '◆', '◇', '■', '□', '▪', '│', '┃',
  '═', '━', '─', '•', '·', '¦', '☯', '✿', '❀', '♛', '♚', '⚑', '⌛', '⏳', '☎', '✉', '©', '®', '™', '∞',
]
