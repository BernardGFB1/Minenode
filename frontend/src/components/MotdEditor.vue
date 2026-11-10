<template>
  <div class="motd-ed" :class="{ compact }">
    <div class="toolbar">
      <div class="grp">
        <button type="button" class="tb" title="Cofnij (Ctrl+Z)" @mousedown.prevent="cmd('undo')"><Undo2 /></button>
        <button type="button" class="tb" title="Ponów (Ctrl+Y)" @mousedown.prevent="cmd('redo')"><Redo2 /></button>
      </div>
      <div class="grp">
        <button type="button" class="tb" :class="{ on: active.bold }" title="Pogrubienie §l" @mousedown.prevent="cmd('bold')"><Bold /></button>
        <button type="button" class="tb" :class="{ on: active.italic }" title="Kursywa §o" @mousedown.prevent="cmd('italic')"><Italic /></button>
        <button type="button" class="tb" :class="{ on: active.underline }" title="Podkreślenie §n" @mousedown.prevent="cmd('underline')"><Underline /></button>
        <button type="button" class="tb" :class="{ on: active.strike }" title="Przekreślenie §m" @mousedown.prevent="cmd('strikeThrough')"><Strikethrough /></button>
        <button type="button" class="tb obf" :class="{ on: active.obf }" title="Zamazany / losowe znaki §k" @mousedown.prevent="toggleObf">§k</button>
        <button type="button" class="tb" title="Wyczyść formatowanie §r" @mousedown.prevent="cmd('removeFormat')"><RemoveFormatting /></button>
      </div>
      <div class="grp pop-wrap">
        <button type="button" class="tb" title="Znaki specjalne" @mousedown.prevent="pop = pop === 'chars' ? null : 'chars'"><Omega /></button>
        <div v-if="pop === 'chars'" class="pop chars" @mousedown.prevent>
          <button v-for="ch in SPECIAL_CHARS" :key="ch" type="button" class="ch" @mousedown.prevent="insert(ch)">{{ ch }}</button>
        </div>
      </div>
      <div class="grp pop-wrap">
        <button type="button" class="tb color" title="Kolor tekstu" @mousedown.prevent="pop = pop === 'color' ? null : 'color'">
          <span class="a" :style="{ borderColor: lastColor }">A</span><ChevronDown class="cv" />
        </button>
        <div v-if="pop === 'color'" class="pop colors" @mousedown.prevent>
          <button v-for="[c, hex, label] in MC_COLORS" :key="c" type="button" class="sw" :style="{ background: hex }" :title="`${label} §${c}`" @mousedown.prevent="color(hex)" />
        </div>
      </div>
      <div v-if="placeholders.length" class="grp ph">
        <button v-for="p in placeholders" :key="p.key" type="button" class="chip" :title="p.label" @mousedown.prevent="insert(`{${p.key}}`)">{{ '{' + p.key + '}' }}</button>
      </div>
      <span class="spacer" />
      <button type="button" class="tb raw" :class="{ on: rawMode }" title="Edytuj kody § ręcznie" @mousedown.prevent="rawMode = !rawMode"><Code2 /> §</button>
    </div>

    <div v-show="!rawMode" ref="ed" class="surface" contenteditable="true" spellcheck="false" :style="surfaceStyle"
         :data-placeholder="placeholder" @input="onInput" @keydown="onKey" @paste="onPaste" @keyup="refreshState" @mouseup="refreshState"
         @blur="pop = null" />
    <textarea v-if="rawMode" v-model="raw" class="surface rawarea" :rows="maxLines" spellcheck="false" :style="surfaceStyle" @input="onRaw" />

    <div class="foot">
      <span>{{ lines }}/{{ maxLines }} {{ maxLines === 1 ? 'linia' : 'linie' }} max</span>
      <span class="mono">{{ visibleLength }} znaków</span>
      <span class="spacer" />
      <span v-if="tooLong" class="warn">Linia ma ponad 45 znaków – na liście serwerów może zostać ucięta</span>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Undo2, Redo2, Bold, Italic, Underline, Strikethrough, RemoveFormatting, Omega, ChevronDown, Code2 } from 'lucide-vue-next'
import { MC_COLORS } from '../utils'
import { MC_FONT, SPECIAL_CHARS, codesToEditorHtml, dirtBackground, serializeEditor } from '../mc'

const props = defineProps({
  modelValue: { type: String, default: '' },
  maxLines: { type: Number, default: 2 },
  placeholders: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Wpisz tekst…' },
  compact: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const ed = ref(null)
const pop = ref(null)
const rawMode = ref(false)
const raw = ref(props.modelValue)
const lastColor = ref('#FFFF55')
const active = reactive({ bold: false, italic: false, underline: false, strike: false, obf: false })
let lastEmitted = null

const surfaceStyle = computed(() => ({
  fontFamily: MC_FONT,
  backgroundImage: `linear-gradient(rgba(0,0,0,.45), rgba(0,0,0,.45)), url(${dirtBackground()})`,
  minHeight: `${props.maxLines * 30 + 22}px`,
}))
const plain = computed(() => props.modelValue.replace(/§[0-9a-fk-or]/gi, ''))
const lines = computed(() => Math.max(1, props.modelValue.split('\n').length))
const visibleLength = computed(() => plain.value.replace(/\n/g, '').length)
const tooLong = computed(() => props.maxLines > 1 && plain.value.split('\n').some((l) => l.replace(/\{[a-z_]+\}/g, 'xxxxxxxxxx').length > 45))

function render(value) {
  if (!ed.value) return
  ed.value.innerHTML = codesToEditorHtml(value)
}
onMounted(() => {
  render(props.modelValue)
  lastEmitted = props.modelValue
})
watch(() => props.modelValue, (v) => {
  if (v !== lastEmitted) { render(v); lastEmitted = v }
  if (!rawMode.value) raw.value = v
})
watch(rawMode, (on) => { if (!on) render(props.modelValue); else raw.value = props.modelValue })

function push() {
  const value = serializeEditor(ed.value)
  lastEmitted = value
  emit('update:modelValue', value)
}
function onInput() { push(); refreshState() }
function onRaw() {
  const v = raw.value.split('\n').slice(0, props.maxLines).join('\n')
  lastEmitted = null
  emit('update:modelValue', v)
}

function focusEditor() {
  if (document.activeElement !== ed.value) ed.value?.focus()
}
function cmd(name, value = null) {
  focusEditor()
  document.execCommand('styleWithCSS', false, true)
  document.execCommand(name, false, value)
  push()
  refreshState()
}
function color(hex) {
  lastColor.value = hex
  pop.value = null
  cmd('foreColor', hex)
}
function insert(text) {
  focusEditor()
  document.execCommand('insertText', false, text)
  pop.value = null
  push()
}
function selectionInObf() {
  const sel = window.getSelection()
  const node = sel?.anchorNode
  const el = node?.nodeType === 3 ? node.parentElement : node
  return !!el?.closest?.('[style*="mcobf"]')
}
function toggleObf() {
  cmd('fontName', selectionInObf() ? 'inherit' : 'mcobf')
}
function refreshState() {
  try {
    active.bold = document.queryCommandState('bold')
    active.italic = document.queryCommandState('italic')
    active.underline = document.queryCommandState('underline')
    active.strike = document.queryCommandState('strikeThrough')
    active.obf = selectionInObf()
  } catch {}
}
function onKey(e) {
  if (e.key === 'Enter') {
    e.preventDefault()
    if (lines.value >= props.maxLines) return
    if (!document.execCommand('insertLineBreak')) document.execCommand('insertHTML', false, '<br>')
    // Chrome needs a trailing <br> to show the caret on a new empty last line
    if (ed.value.lastChild?.nodeName !== 'BR') ed.value.appendChild(document.createElement('br'))
    push()
  }
}
function onPaste(e) {
  e.preventDefault()
  let text = (e.clipboardData?.getData('text/plain') || '').replace(/\r/g, '')
  const room = props.maxLines - lines.value
  const parts = text.split('\n')
  text = parts.slice(0, room + 1).join('\n')
  if (/§[0-9a-fk-or]/i.test(text)) {
    // pasted § codes: merge into the raw value so formatting is preserved
    const v = (props.modelValue + text).split('\n').slice(0, props.maxLines).join('\n')
    lastEmitted = null
    emit('update:modelValue', v)
    return
  }
  document.execCommand('insertText', false, text)
  push()
}
</script>
<style scoped>
.motd-ed { border: 1px solid var(--border-strong); border-radius: var(--radius); overflow: hidden; background: var(--surface-2); }
.toolbar { display: flex; align-items: center; gap: 6px; padding: 6px; border-bottom: 1px solid var(--border); flex-wrap: wrap; background: var(--surface); }
.grp { display: inline-flex; gap: 1px; padding: 2px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface-2); }
.tb { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 28px; height: 26px; padding: 0 6px; border: 0; border-radius: 5px;
  background: none; color: var(--text-2); cursor: pointer; font: inherit; font-size: 12.5px; }
.tb svg { width: 15px; height: 15px; }
.tb:hover { background: var(--surface-3); color: var(--text); }
.tb.on { background: var(--accent-soft); color: var(--accent); }
.tb.obf { font-family: var(--mono); font-weight: 700; }
.tb.color .a { font-weight: 700; border-bottom: 3px solid; line-height: 1; padding: 0 2px 1px; }
.tb.color .cv { width: 12px; height: 12px; }
.pop-wrap { position: relative; }
.pop { position: absolute; top: calc(100% + 6px); left: 0; z-index: 20; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); padding: 8px; }
.colors { display: grid; grid-template-columns: repeat(8, 22px); gap: 5px; }
.sw { width: 22px; height: 22px; border-radius: 4px; border: 1px solid rgba(255, 255, 255, 0.2); cursor: pointer; }
.sw:hover { transform: scale(1.12); }
.chars { display: grid; grid-template-columns: repeat(10, 28px); gap: 2px; }
.ch { width: 28px; height: 28px; border: 0; border-radius: 5px; background: none; color: var(--text); font-size: 15px; cursor: pointer; }
.ch:hover { background: var(--surface-3); }
.ph { border: 0; background: none; gap: 4px; }
.chip { height: 24px; font-family: var(--mono); font-size: 11.5px; padding: 0 8px; }
.raw { font-family: var(--mono); }
.spacer { flex: 1; }
.surface { display: block; width: 100%; padding: 10px 14px; color: #aaaaaa; font-size: 21px; line-height: 30px; background-size: auto, 48px 48px;
  image-rendering: pixelated; text-shadow: 2.5px 2.5px 0 rgba(0, 0, 0, 0.6); outline: none; white-space: pre-wrap; word-break: break-word; border: 0; resize: none; }
.compact .surface { font-size: 18px; line-height: 26px; }
.surface:empty::before { content: attr(data-placeholder); color: #6d6d6d; }
.surface :deep([style*='mcobf']) { font-family: inherit !important; background: repeating-linear-gradient(135deg, rgba(255, 255, 255, 0.16) 0 3px, transparent 3px 6px); border-radius: 2px; }
.rawarea { font-family: var(--mono) !important; font-size: 14px; line-height: 24px; color: #e4e9e5; }
.foot { display: flex; gap: 14px; padding: 5px 10px; font-size: 11.5px; color: var(--muted); border-top: 1px solid var(--border); }
.foot .warn { color: var(--warn); }
</style>
