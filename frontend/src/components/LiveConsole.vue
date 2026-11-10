<template>
  <div class="console" :class="{ fill }">
    <div class="bar">
      <div class="row grow" style="gap: 6px">
        <span class="badge" :class="connected ? 'green' : 'amber'"><span class="dot" />{{ connected ? 'Na żywo' : 'Łączenie…' }}</span>
        <span class="tiny muted num">{{ lines.length }} linii</span>
      </div>
      <div class="search">
        <Search />
        <input v-model="query" placeholder="Filtruj…" />
      </div>
      <div class="btn-group">
        <button class="btn sm" :class="{ active: levels.warn }" title="Tylko ostrzeżenia i błędy" @click="levels.warn = !levels.warn"><AlertTriangle /></button>
        <button class="btn sm" :class="{ active: wrap }" title="Zawijanie linii" @click="wrap = !wrap"><WrapText /></button>
        <button class="btn sm" title="Wyczyść widok" @click="clearView"><Eraser /></button>
      </div>
    </div>
    <div ref="scroller" class="out" :class="{ wrap }" @scroll="onScroll">
      <div v-for="l in visible" :key="l.seq" class="ln" :class="[l.src, l.level]">
        <span class="ts">{{ time(l.t) }}</span>
        <span class="tx" v-html="l.html" />
      </div>
      <div v-if="!visible.length" class="muted small" style="padding: 12px">Konsola jest pusta.</div>
    </div>
    <button v-if="!stick" class="jump btn sm" @click="toBottom"><ArrowDown /> Nowe linie</button>
    <form class="input-row" @submit.prevent="submit">
      <ChevronRight class="prompt" />
      <input ref="input" v-model="cmd" :disabled="!canSend" :placeholder="canSend ? 'Wpisz komendę (bez /), ↑↓ historia' : 'Serwer nie działa'"
             autocomplete="off" spellcheck="false" @keydown.up.prevent="hist(-1)" @keydown.down.prevent="hist(1)" />
      <button class="btn sm primary" :disabled="!canSend || !cmd.trim()">Wyślij</button>
    </form>
  </div>
</template>
<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { Search, AlertTriangle, WrapText, Eraser, ArrowDown, ChevronRight } from 'lucide-vue-next'
import { ansiToHtml } from '../utils'

const props = defineProps({ entries: Array, connected: Boolean, canSend: Boolean, fill: Boolean })
const emit = defineEmits(['command'])
const scroller = ref(null)
const input = ref(null)
const cmd = ref('')
const query = ref('')
const wrap = ref(true)
const stick = ref(true)
const levels = reactive({ warn: false })
const clearedAt = ref(0)
const history = []
let hi = -1

const LEVEL_RE = /\/(WARN|ERROR|FATAL)\]|\[(WARN|ERROR|FATAL)\]|^\s*(WARN|ERROR)\b/
const cache = new Map()
function decorate(e) {
  let d = cache.get(e.seq)
  if (!d) {
    const m = e.text.match(LEVEL_RE)
    const lvl = m ? (m[1] || m[2] || m[3]).toLowerCase() : ''
    d = { ...e, level: lvl === 'fatal' ? 'error' : lvl, html: ansiToHtml(e.text) }
    cache.set(e.seq, d)
    if (cache.size > 6000) cache.delete(cache.keys().next().value)
  }
  return d
}
const lines = computed(() => props.entries.filter((e) => e.seq > clearedAt.value))
const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return lines.value.map(decorate).filter((l) => (!q || l.text.toLowerCase().includes(q)) && (!levels.warn || l.level))
    .slice(-2500)
})
const time = (t) => new Date(t * 1000).toLocaleTimeString('pl-PL', { hour12: false })

function onScroll() {
  const el = scroller.value
  stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
function toBottom() {
  stick.value = true
  nextTick(() => scroller.value && (scroller.value.scrollTop = scroller.value.scrollHeight))
}
watch(() => visible.value.length && visible.value.at(-1)?.seq, () => stick.value && toBottom(), { flush: 'post' })
watch(() => props.entries.length, (n, o) => !o && toBottom())

function clearView() { clearedAt.value = props.entries.at(-1)?.seq || 0 }
function submit() {
  const c = cmd.value.trim()
  if (!c) return
  emit('command', c)
  if (history.at(-1) !== c) history.push(c)
  hi = history.length
  cmd.value = ''
  toBottom()
}
function hist(d) {
  if (!history.length) return
  hi = Math.max(0, Math.min(history.length, hi + d))
  cmd.value = history[hi] || ''
}
defineExpose({ focus: () => input.value?.focus() })
</script>
<style scoped>
.console { position: relative; display: flex; flex-direction: column; background: var(--console-bg); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; height: 520px; color: #d5dbd6; }
.console.fill { height: calc(100vh - 250px); min-height: 380px; }
.bar { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); }
.bar .btn { background: transparent; border-color: rgba(255, 255, 255, 0.1); color: #a9b3ac; }
.bar .btn.active { color: var(--accent); background: rgba(134, 192, 98, 0.1); }
.search { display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 9px; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.1); }
.search svg { width: 14px; height: 14px; color: #7b867f; }
.search input { background: none; border: 0; outline: none; color: #d5dbd6; font: inherit; font-size: 12.5px; width: 140px; }
.out { flex: 1; overflow: auto; padding: 8px 0; font-family: var(--mono); font-size: 12.5px; line-height: 1.55; }
.ln { display: flex; gap: 12px; padding: 0 14px; white-space: pre; }
.out.wrap .ln { white-space: pre-wrap; word-break: break-word; }
.ln:hover { background: rgba(255, 255, 255, 0.03); }
.ts { color: #56605a; flex: none; user-select: none; }
.ln.warn .tx { color: #e3b866; }
.ln.error .tx { color: #ef8478; }
.ln.panel .tx { color: #8fc7ec; }
.ln.cmd .tx { color: #a6e07f; }
.ln.cmd .tx::before { content: '> '; color: #56605a; }
.jump { position: absolute; right: 18px; bottom: 60px; background: var(--surface-2); }
.input-row { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-top: 1px solid rgba(255, 255, 255, 0.06); }
.prompt { width: 16px; height: 16px; color: var(--accent); flex: none; }
.input-row input { flex: 1; background: none; border: 0; outline: none; color: #e4e9e5; font-family: var(--mono); font-size: 13px; height: 30px; }
.input-row input:disabled { opacity: 0.5; }
</style>
