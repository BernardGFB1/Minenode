import { t, dateLocale } from './i18n'

export function formatBytes(n, digits = 1) {
  if (n == null || isNaN(n)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i === 0 ? 0 : digits)} ${units[i]}`
}

export function formatMB(mb) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 ? 1 : 0)} GB` : `${mb} MB`
}

export function formatDuration(sec) {
  if (sec == null) return '—'
  sec = Math.max(0, Math.floor(sec))
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60)
  if (d) return `${d} d ${h} h`
  if (h) return `${h} h ${m} min`
  if (m) return `${m} min`
  return `${sec} s`
}

export function timeAgo(ts) {
  if (!ts) return '—'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return t('przed chwilą')
  if (diff < 3600) return t('{n} min temu', { n: Math.floor(diff / 60) })
  if (diff < 86400) return t('{n} h temu', { n: Math.floor(diff / 3600) })
  if (diff < 86400 * 30) return t('{n} dni temu', { n: Math.floor(diff / 86400) })
  return new Date(ts * 1000).toLocaleDateString(dateLocale())
}

export function formatDate(ts) {
  if (!ts) return '—'
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return d.toLocaleString(dateLocale(), { dateStyle: 'medium', timeStyle: 'short' })
}

export const compact = (n) => new Intl.NumberFormat(dateLocale(), { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0)

export const STATE = {
  online: { label: 'Online', color: 'green' },
  starting: { label: 'Uruchamianie', color: 'amber' },
  stopping: { label: 'Zatrzymywanie', color: 'amber' },
  offline: { label: 'Offline', color: '' },
  crashed: { label: 'Awaria', color: 'red' },
  installing: { label: 'Instalacja', color: 'blue' },
  install_failed: { label: 'Instalacja nieudana', color: 'red' },
  not_installed: { label: 'Niezainstalowany', color: '' },
}

/** Card outline tone for a server state: green = running, red = stopped/crashed, amber = in between. */
export function stateTone(state) {
  if (state === 'online') return 'on'
  if (['starting', 'stopping', 'installing'].includes(state)) return 'busy'
  return 'off'
}

export const ENGINE_META = {
  vanilla: { name: 'Vanilla', color: '#7fa65a', letter: 'V' },
  paper: { name: 'Paper', color: '#e0e6ea', letter: 'P', ink: '#1d2a33' },
  purpur: { name: 'Purpur', color: '#a77bd1', letter: 'Pu' },
  fabric: { name: 'Fabric', color: '#d8c7a3', letter: 'F', ink: '#3a2f1d' },
  quilt: { name: 'Quilt', color: '#c45ebf', letter: 'Q' },
  forge: { name: 'Forge', color: '#d98b4b', letter: 'Fo' },
  neoforge: { name: 'NeoForge', color: '#e3a65b', letter: 'N', ink: '#3b2612' },
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

/** Render Minecraft § formatting codes as HTML spans. */
export function mcToHtml(text) {
  if (!text) return ''
  const parts = text.split(/[§&](?=[0-9a-fk-or])/i)
  let html = escapeHtml(parts[0])
  let color = null
  const styles = new Set()
  for (let i = 1; i < parts.length; i++) {
    const code = parts[i][0].toLowerCase()
    const rest = parts[i].slice(1)
    if (/[0-9a-f]/.test(code)) { color = code; styles.clear() }
    else if (code === 'r') { color = null; styles.clear() }
    else if ('lomnk'.includes(code)) styles.add(code)
    if (!rest) continue
    const cls = [color && `mc-${color}`, ...[...styles].map((s) => `mc-${s}`)].filter(Boolean).join(' ')
    html += cls ? `<span class="${cls}">${escapeHtml(rest)}</span>` : escapeHtml(rest)
  }
  return html.replace(/\\n|\n/g, '<br>')
}

// ---------------------------------------------------------------- ANSI → HTML (console)
const ANSI_16 = ['#3b3f3c', '#e5675a', '#86c062', '#e3a849', '#64a8d8', '#c678dd', '#56b6c2', '#c9cfca',
  '#6b736d', '#ff8577', '#a6e07f', '#ffd27a', '#8cc8f2', '#e09bf0', '#7fdbe6', '#ffffff']

function xterm256(n) {
  if (n < 16) return ANSI_16[n]
  if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})` }
  n -= 16
  const c = [Math.floor(n / 36), Math.floor((n % 36) / 6), n % 6].map((x) => (x ? x * 40 + 55 : 0))
  return `rgb(${c.join(',')})`
}

export function ansiToHtml(text) {
  // eslint-disable-next-line no-control-regex
  const re = /\x1b\[([0-9;]*)m/g
  let out = ''
  let last = 0
  let state = { fg: null, bold: false, italic: false, underline: false }
  let open = false
  const openSpan = () => {
    const st = []
    if (state.fg) st.push(`color:${state.fg}`)
    if (state.bold) st.push('font-weight:700')
    if (state.italic) st.push('font-style:italic')
    if (state.underline) st.push('text-decoration:underline')
    if (st.length) { out += `<span style="${st.join(';')}">`; open = true }
  }
  let m
  while ((m = re.exec(text))) {
    out += escapeHtml(text.slice(last, m.index))
    last = re.lastIndex
    if (open) { out += '</span>'; open = false }
    const codes = m[1] === '' ? [0] : m[1].split(';').map(Number)
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i]
      if (c === 0) state = { fg: null, bold: false, italic: false, underline: false }
      else if (c === 1) state.bold = true
      else if (c === 3) state.italic = true
      else if (c === 4) state.underline = true
      else if (c === 22) state.bold = false
      else if (c === 39) state.fg = null
      else if (c >= 30 && c <= 37) state.fg = ANSI_16[c - 30]
      else if (c >= 90 && c <= 97) state.fg = ANSI_16[c - 90 + 8]
      else if (c === 38 && codes[i + 1] === 5) { state.fg = xterm256(codes[i + 2]); i += 2 }
      else if (c === 38 && codes[i + 1] === 2) { state.fg = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`; i += 4 }
    }
    openSpan()
  }
  out += escapeHtml(text.slice(last))
  if (open) out += '</span>'
  return out
}

export const MC_COLORS = [
  ['0', '#000000', 'Czarny'], ['1', '#0000AA', 'Granatowy'], ['2', '#00AA00', 'Zielony'], ['3', '#00AAAA', 'Morski'],
  ['4', '#AA0000', 'Bordowy'], ['5', '#AA00AA', 'Fioletowy'], ['6', '#FFAA00', 'Złoty'], ['7', '#AAAAAA', 'Szary'],
  ['8', '#555555', 'Ciemnoszary'], ['9', '#5555FF', 'Niebieski'], ['a', '#55FF55', 'Jasnozielony'], ['b', '#55FFFF', 'Błękitny'],
  ['c', '#FF5555', 'Czerwony'], ['d', '#FF55FF', 'Różowy'], ['e', '#FFFF55', 'Żółty'], ['f', '#FFFFFF', 'Biały'],
]

export function slugify(s) {
  return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/ł/g, 'l')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true } catch { return false }
}
