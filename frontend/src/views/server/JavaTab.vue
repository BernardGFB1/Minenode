<template>
  <div v-if="info && j" class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start">
    <div class="col" style="gap: 16px">
      <section class="card">
        <div class="card-head"><Coffee class="hi" /><h3>{{ $t('Środowisko Java') }}</h3></div>
        <div class="card-body col" style="gap: 14px">
          <div class="jopts">
            <label class="jopt" :class="{ active: j.version == null }">
              <input v-model="j.version" type="radio" :value="null" />
              <div><b>{{ $t('Automatycznie') }}</b><div class="tiny muted">{{ $t('Java {j} dla MC {mc}', { j: autoFeature, mc: live.summary.mc_version }) }}</div></div>
              <span class="badge green">{{ $t('zalecane') }}</span>
            </label>
            <label v-for="v in info.available_java" :key="v" class="jopt" :class="{ active: j.version === v, warn: v < info.required_java }">
              <input v-model="j.version" type="radio" :value="v" />
              <div><b>Java {{ v }}</b><div class="tiny muted">{{ info.installed_java.includes(v) ? $t('pobrana') : $t('zostanie pobrana przy starcie') }}</div></div>
              <span v-if="v < info.required_java" class="badge red">{{ $t('za stara') }}</span>
            </label>
          </div>
          <div v-if="j.version && j.version < info.required_java" class="callout warn small"><AlertTriangle />{{ $t('Minecraft {mc} wymaga Javy {j}+. Serwer najpewniej się nie uruchomi.', { mc: live.summary.mc_version, j: info.required_java }) }}</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><MemoryStick class="hi" /><h3>{{ $t('Pamięć') }}</h3></div>
        <div class="card-body">
          <RamPicker v-model:min="j.min_ram_mb" v-model:max="j.max_ram_mb" :total-bytes="info.mem_total" :other-bytes="info.allocated_other" />
        </div>
      </section>

      <section class="card">
        <div class="card-head"><Zap class="hi" /><h3>{{ $t('Optymalizacje JVM') }}</h3></div>
        <div class="card-body col" style="gap: 10px">
          <label v-for="p in presets" :key="p.id" class="preset" :class="{ active: j.flags_preset === p.id }">
            <input v-model="j.flags_preset" type="radio" :value="p.id" />
            <div class="grow"><div class="row" style="gap: 8px"><b>{{ $t(p.name) }}</b><span v-if="p.badge" class="badge" :class="p.badgeColor">{{ $t(p.badge) }}</span></div>
              <div class="small muted">{{ $t(p.desc) }}</div></div>
          </label>
        </div>
      </section>
    </div>

    <div class="col" style="gap: 16px">
      <section class="card">
        <div class="card-head"><Terminal class="hi" /><h3>{{ $t('Dodatkowe argumenty') }}</h3></div>
        <div class="card-body col" style="gap: 14px">
          <div class="field">
            <label>{{ $t('Flagi JVM') }}</label>
            <textarea v-model="j.extra_jvm_args" class="textarea mono" rows="3" :placeholder="$t('-Dpaper.disableChannelLimit=true -XX:+UseLargePages')" />
            <span class="hint">{{ $t('Dodawane po flagach z presetu. Nieznane flagi -XX są bezpiecznie ignorowane.') }}</span>
          </div>
          <div class="field">
            <label>{{ $t('Argumenty serwera') }}</label>
            <input v-model="j.extra_server_args" class="input mono" :placeholder="$t('--forceUpgrade')" />
            <span class="hint">{{ $t('Przekazywane do serwera po') }} <span class="mono">nogui</span>.</span>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><SquareTerminal class="hi" /><h3>{{ $t('Komenda startowa') }}</h3>
          <button class="btn sm ghost" @click="copyCmd"><Copy /> {{ $t('Kopiuj') }}</button></div>
        <pre class="cmd"><span v-for="(a, i) in info.command" :key="i" :class="cls(a, i)">{{ a }} </span></pre>
        <div class="card-body tiny muted" style="padding-top: 0">{{ $t('Podgląd odświeża się po zapisaniu. Zmiany obowiązują od następnego uruchomienia.') }}</div>
      </section>
    </div>

    <Transition name="fade">
      <div v-if="dirty" class="savebar card" style="grid-column: 1 / -1">
        <span class="small">{{ $t('Niezapisane zmiany ustawień uruchamiania') }}</span><span class="spacer" />
        <button class="btn ghost" @click="reset">{{ $t('Odrzuć') }}</button>
        <button class="btn primary" :disabled="saving" @click="save"><Save /> {{ $t('Zapisz') }}</button>
      </div>
    </Transition>
  </div>
  <div v-else class="empty"><Loader2 class="spin" /></div>
</template>
<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { Coffee, MemoryStick, Zap, Terminal, SquareTerminal, Copy, Save, AlertTriangle, Loader2 } from 'lucide-vue-next'
import RamPicker from '../../components/RamPicker.vue'
import { api } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { copyText } from '../../utils'

const live = inject('live')
const id = live.summary.id
const info = ref(null)
const j = ref(null)
const saving = ref(false)
const presets = [
  { id: 'aikar', name: "Flagi Aikara (G1GC)", badge: 'zalecane', badgeColor: 'green', desc: 'Sprawdzony zestaw strojenia G1 dla serwerów Minecraft – krótkie pauzy GC, automatycznie skalowany dla >12 GB.' },
  { id: 'zgc', name: 'Generational ZGC', badge: 'Java 17+', badgeColor: 'blue', desc: 'Pauzy poniżej milisekundy. Dobre dla dużych stert (16 GB+), wymaga trochę więcej RAM.' },
  { id: 'none', name: 'Bez optymalizacji', desc: 'Tylko -Xms/-Xmx. Przydatne do diagnozowania problemów.' },
]
const autoFeature = computed(() => info.value?.available_java.find((v) => v >= info.value.required_java) || info.value?.required_java)
const original = ref('')
const dirty = computed(() => j.value && JSON.stringify(j.value) !== original.value)

async function load() {
  try {
    info.value = await api.get(`/servers/${id}/startup`)
    const d = await api.get(`/servers/${id}`)
    j.value = { ...d.config.java }
    original.value = JSON.stringify(j.value)
  } catch (e) { toastError(e) }
}
onMounted(load)
function reset() { j.value = JSON.parse(original.value) }
async function save() {
  saving.value = true
  try {
    await api.patch(`/servers/${id}/config`, { java: j.value })
    await load()
    if (live.summary.state === 'online' && (await confirmDialog({ title: 'Zapisano – zrestartować serwer?', message: 'Nowe ustawienia Javy zostaną użyte po restarcie.', confirmText: 'Restartuj' }))) {
      await api.post(`/servers/${id}/power`, { action: 'restart' })
    } else toast('Zapisano ustawienia uruchamiania', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
}
const cls = (a, i) => (i === 0 ? 'c-bin' : a.startsWith('-Xm') ? 'c-mem' : a.startsWith('-XX') ? 'c-xx' : a.startsWith('-D') ? 'c-d' : a === '-jar' || a.startsWith('@') ? 'c-jar' : '')
async function copyCmd() { if (await copyText(info.value.command.join(' '))) toast('Skopiowano', 'success', 1500) }
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
@media (max-width: 1000px) { .grid[style] { grid-template-columns: 1fr !important; } }
.jopts { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px; }
.jopt, .preset { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface-2); cursor: pointer; }
.jopt input, .preset input { accent-color: var(--accent); }
.jopt.active, .preset.active { border-color: var(--accent); background: var(--accent-soft); }
.jopt.warn.active { border-color: var(--danger); background: var(--danger-soft); }
.jopt > div { flex: 1; }
.cmd { margin: 0; padding: 14px 18px; background: var(--console-bg); color: #cfd6d0; font-family: var(--mono); font-size: 12px; line-height: 1.7; white-space: pre-wrap; word-break: break-all; max-height: 340px; overflow: auto; }
.c-bin { color: #8fc7ec; } .c-mem { color: #a6e07f; font-weight: 600; } .c-xx { color: #c9cfca; } .c-d { color: #e3b866; } .c-jar { color: #e09bf0; }
.savebar { position: sticky; bottom: 16px; display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-color: var(--accent); z-index: 5; }
</style>
