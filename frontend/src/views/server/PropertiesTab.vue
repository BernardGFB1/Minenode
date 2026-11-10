<template>
  <div v-if="cur" class="col" style="gap: 16px">
    <div v-if="!exists" class="callout info small"><Info />Plik server.properties zostanie w pełni wygenerowany przy pierwszym uruchomieniu. Możesz już teraz ustawić najważniejsze opcje.</div>
    <div class="layout">
      <nav class="side card">
        <a v-for="g in GROUPS" :key="g.id" :href="`#g-${g.id}`" class="side-link" @click.prevent="scrollTo(g.id)">
          <component :is="icons[g.icon]" /> {{ g.title }}
        </a>
        <a href="#g-raw" class="side-link" @click.prevent="scrollTo('raw')"><Braces /> Wszystkie klucze</a>
      </nav>
      <div class="col" style="gap: 16px; min-width: 0">
        <section v-for="g in GROUPS" :id="`g-${g.id}`" :key="g.id" class="card">
          <div class="card-head"><component :is="icons[g.icon]" class="hi" /><h3>{{ g.title }}</h3></div>
          <div class="card-body fields">
            <template v-for="f in g.fields" :key="f.key">
              <div v-if="visible(f)" class="field" :class="{ wide: f.wide || f.type === 'motd', boolf: f.type === 'bool' }">
                <template v-if="f.type === 'bool'">
                  <Toggle :model-value="val(f) === 'true'" :label="f.label" :hint="f.hint" @update:model-value="set(f.key, $event ? 'true' : 'false')" />
                </template>
                <template v-else>
                  <label>{{ f.label }} <span class="key mono">{{ f.key }}</span></label>
                  <select v-if="f.type === 'select'" class="select" :value="val(f)" @change="set(f.key, $event.target.value)">
                    <option v-for="[v, l] in f.options" :key="v" :value="v">{{ l }}</option>
                    <option v-if="!f.options.some(([v]) => v === val(f))" :value="val(f)">{{ val(f) }}</option>
                  </select>
                  <div v-else-if="f.type === 'range'" class="row">
                    <input type="range" :min="f.min" :max="f.max" :value="val(f)" @input="set(f.key, $event.target.value)" />
                    <span class="num mono rv">{{ val(f) }}</span>
                  </div>
                  <template v-else-if="f.type === 'motd'">
                    <MotdEditor :model-value="val(f)" :max-lines="2" @update:model-value="set(f.key, $event)" />
                    <div class="section-title" style="margin: 6px 0 0">Podgląd na liście serwerów</div>
                    <ServerListEntry :name="live.summary.name" :motd="val(f)" :icon="live.summary.has_icon ? `/api/servers/${live.summary.id}/icon` : null"
                                     :players="`${live.summary.players_online}/${edits['max-players'] ?? cur['max-players'] ?? 20}`" />
                  </template>
                  <input v-else class="input" :class="{ mono: f.mono }" :type="f.type === 'number' ? 'number' : 'text'" :min="f.min" :max="f.max"
                         :placeholder="f.placeholder" :value="val(f)" @input="set(f.key, $event.target.value)" />
                  <span v-if="f.hint" class="hint">{{ f.hint }}</span>
                </template>
              </div>
            </template>
          </div>
        </section>

        <section id="g-raw" class="card">
          <div class="card-head"><Braces class="hi" /><h3>Wszystkie klucze</h3><input v-model="rawFilter" class="input" style="width: 220px; height: 30px" placeholder="Filtruj…" /></div>
          <table class="table raw">
            <tbody>
              <tr v-for="k in rawKeys" :key="k">
                <td class="mono small" style="width: 40%">{{ k }}</td>
                <td>
                  <input v-if="!managed.includes(k)" class="input mono" style="height: 30px" :value="edits[k] ?? cur[k]" @input="set(k, $event.target.value)" />
                  <span v-else class="small muted mono">{{ cur[k] }} <span class="badge">zarządzane przez panel (proxy)</span></span>
                </td>
              </tr>
            </tbody>
          </table>
          <form class="card-body row" style="border-top: 1px solid var(--border)" @submit.prevent="addKey">
            <input v-model="newKey" class="input mono" placeholder="nowy-klucz" />
            <input v-model="newVal" class="input mono" placeholder="wartość" />
            <button class="btn" :disabled="!newKey"><Plus /> Dodaj</button>
          </form>
        </section>
      </div>
    </div>

    <Transition name="fade">
      <div v-if="dirty" class="savebar card">
        <span class="small"><b>{{ Object.keys(edits).length }}</b> niezapisanych zmian</span>
        <span class="spacer" />
        <button class="btn ghost" @click="edits = {}">Odrzuć</button>
        <button class="btn primary" :disabled="saving" @click="save"><Save /> Zapisz {{ live.summary.state === 'online' ? 'i uruchom ponownie' : '' }}</button>
      </div>
    </Transition>
  </div>
  <div v-else class="empty"><Loader2 class="spin" /></div>
</template>
<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { Sparkles, Globe2, ShieldCheck, Gauge, Image, Braces, Plus, Save, Info, Loader2 } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import MotdEditor from '../../components/MotdEditor.vue'
import ServerListEntry from '../../components/ServerListEntry.vue'
import { GROUPS } from '../../data/properties'
import { api } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'

const icons = { Sparkles, Globe2, ShieldCheck, Gauge, Image }
const live = inject('live')
const id = live.summary.id
const cur = ref(null)
const managed = ref([])
const exists = ref(true)
const edits = ref({})
const saving = ref(false)
const rawFilter = ref('')
const newKey = ref('')
const newVal = ref('')

const dirty = computed(() => Object.keys(edits.value).length > 0)
const val = (f) => edits.value[f.key] ?? cur.value[f.key] ?? f.default
const visible = (f) => !exists.value || f.key in cur.value || ['motd', 'max-players', 'difficulty', 'gamemode', 'pvp', 'online-mode', 'white-list', 'level-seed', 'view-distance'].includes(f.key)
const set = (k, v) => { edits.value = { ...edits.value, [k]: String(v) } }
const rawKeys = computed(() => Object.keys({ ...cur.value, ...edits.value }).filter((k) => !rawFilter.value || k.includes(rawFilter.value)).sort())

async function load() {
  try {
    const r = await api.get(`/servers/${id}/properties`)
    cur.value = r.properties
    managed.value = r.managed
    exists.value = r.exists
  } catch (e) { toastError(e) }
}
onMounted(load)

function addKey() {
  if (!/^[\w.-]+$/.test(newKey.value)) return toast('Niepoprawny klucz', 'error')
  set(newKey.value, newVal.value)
  newKey.value = ''
  newVal.value = ''
}

async function save() {
  saving.value = true
  try {
    const r = await api.put(`/servers/${id}/properties`, { properties: edits.value })
    cur.value = r.properties
    edits.value = {}
    if (live.summary.state === 'online') {
      if (await confirmDialog({ title: 'Zapisano – uruchomić ponownie?', message: 'Zmiany w server.properties zostaną zastosowane po restarcie serwera.', confirmText: 'Restartuj teraz' })) {
        await api.post(`/servers/${id}/power`, { action: 'restart' })
      }
    } else toast('Zapisano konfigurację', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
}
function scrollTo(g) { document.getElementById(`g-${g}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
</script>
<style scoped>
.layout { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 16px; align-items: start; }
.side { position: sticky; top: 16px; padding: 8px; display: flex; flex-direction: column; gap: 2px; }
.side-link { display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: var(--radius-sm); color: var(--text-2); font-size: 13px; font-weight: 500; }
.side-link:hover { background: var(--surface-3); color: var(--text); text-decoration: none; }
.side-link svg, .hi { width: 16px; height: 16px; color: var(--muted); }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .side { display: none; } }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 22px; }
.fields .wide { grid-column: 1 / -1; }
.boolf { justify-content: center; }
@media (max-width: 720px) { .fields { grid-template-columns: 1fr; } }
.key { font-size: 11px; color: var(--muted); font-weight: 400; margin-left: 4px; }
.rv { min-width: 32px; text-align: right; }
.raw td { padding: 6px 14px; }
.savebar { position: sticky; bottom: 16px; display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-color: var(--accent); z-index: 5; }
</style>
