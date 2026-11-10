// Lightweight i18n: Polish source strings are the keys, English is a dictionary (locales/en.js).
// Missing translations fall back to the Polish text, so new features never break the UI.
import { reactive } from 'vue'
import en from './locales/en'

const LOCALES = ['pl', 'en']
const stored = (() => { try { return localStorage.getItem('minenode-lang') } catch { return null } })()
const guess = (navigator.language || 'en').toLowerCase().startsWith('pl') ? 'pl' : 'en'

export const i18n = reactive({ locale: LOCALES.includes(stored) ? stored : guess })

const fill = (s, params) => (params ? s.replace(/\{(\w+)\}/g, (m, k) => (params[k] ?? m)) : s)

/** Translate a UI string. `params` fills {name} placeholders. */
export function t(key, params) {
  const s = i18n.locale === 'en' ? (en.strings[key] ?? key) : key
  return fill(s, params)
}

// Messages produced by the backend (errors, console notes, job stages) are matched against templates.
const compiled = en.patterns.map(([pl, eng]) => {
  const names = []
  const re = new RegExp('^' + pl.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{(\w+)\}/g, (m, k) => { names.push(k); return '([\\s\\S]*?)' }) + '$')
  return { re, names, eng }
})

/** Translate a free-form message coming from the server. */
export function tm(msg) {
  if (!msg || i18n.locale !== 'en') return msg
  if (en.strings[msg]) return en.strings[msg]
  for (const { re, names, eng } of compiled) {
    const m = msg.match(re)
    if (m) return eng.replace(/\{(\w+)\}/g, (x, k) => { const i = names.indexOf(k); return i >= 0 ? m[i + 1] : x })
  }
  return msg
}

export function setLocale(l) {
  if (!LOCALES.includes(l)) return
  i18n.locale = l
  document.documentElement.lang = l
  try { localStorage.setItem('minenode-lang', l) } catch {}
}

export const dateLocale = () => (i18n.locale === 'en' ? 'en-GB' : 'pl-PL')

export default {
  install(app) {
    document.documentElement.lang = i18n.locale
    app.config.globalProperties.$t = t
    app.config.globalProperties.$tm = tm
    // server addresses use a placeholder host until a domain is configured
    app.config.globalProperties.$addr = (a) => (a ? a.replace('twoja-domena', t('twoja-domena')) : a)
  },
}
