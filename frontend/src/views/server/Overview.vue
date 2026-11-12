<template>
  <div class="col" style="gap: 16px">
    <div class="grid grid-4">
      <div class="card tile">
        <div class="tl"><Timer /> {{ $t('Czas działania') }}</div>
        <div class="tv num">{{ s.started_at ? formatDuration(now - s.started_at) : '—' }}</div>
        <div class="tiny muted">{{ s.state === 'crashed' ? $t('ostatni kod wyjścia: {code}', { code: s.exit_code }) : $t(stateText) }}</div>
      </div>
      <div class="card tile">
        <div class="tl"><Users /> {{ $t('Gracze') }}</div>
        <div class="tv num">{{ s.players_online }}<small> / {{ s.max_players }}</small></div>
        <Meter :value="s.players_online / (s.max_players || 1)" />
      </div>
      <div class="card tile">
        <div class="tl"><Cpu /> CPU</div>
        <div class="tv num">{{ running ? Math.round(s.cpu) : '—' }}<small v-if="running">%</small></div>
        <div class="tiny muted">{{ $t('100% = 1 rdzeń · host ma {n}', { n: cpuCount }) }}</div>
      </div>
      <div class="card tile">
        <div class="tl"><MemoryStick /> {{ $t('Pamięć procesu') }}</div>
        <div class="tv num">{{ running && s.mem ? formatBytes(s.mem) : '—' }}</div>
        <Meter :value="s.mem / (s.max_ram_mb * 1048576 * 1.15)" />
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h3>{{ $t('Obciążenie CPU') }}</h3><span class="tiny muted">{{ $t('ostatnie {w}', { w: windowLabel }) }}</span></div>
        <div class="card-body"><AreaChart :points="cpuPts" :window="windowSec" :format="(v) => v.toFixed(0) + '%'" /></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>{{ $t('Zużycie pamięci') }}</h3><span class="tiny muted">{{ $t('limit sterty {v}', { v: formatMB(s.max_ram_mb) }) }}</span></div>
        <div class="card-body"><AreaChart :points="memPts" :window="windowSec" :max="s.max_ram_mb * 1048576 * 1.2" color="var(--info)" :format="(v) => formatBytes(v)" /></div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr)">
      <div class="card">
        <div class="card-head"><h3>{{ $t('Ostatnie logi') }}</h3><router-link :to="`/servers/${s.id}/console`" class="btn sm ghost">{{ $t('Otwórz konsolę') }} <ArrowRight /></router-link></div>
        <div class="tail mono">
          <div v-for="l in tail" :key="l.seq" class="tl-line" :class="l.src" v-html="ansiToHtml(l.src === 'panel' ? $tm(l.text) : l.text)" />
          <div v-if="!tail.length" class="muted small">{{ $t('Brak logów.') }}</div>
        </div>
      </div>
      <div class="col" style="gap: 16px">
        <div class="card">
          <div class="card-head"><h3>{{ $t('Online teraz') }}</h3><router-link :to="`/servers/${s.id}/players`" class="btn sm ghost">{{ $t('Gracze') }} <ArrowRight /></router-link></div>
          <div class="card-body">
            <div v-if="!s.players?.length" class="muted small">{{ $t('Nikt nie gra.') }}</div>
            <div v-else class="row wrap" style="gap: 8px">
              <span v-for="p in s.players" :key="p" class="player"><img :src="`https://mc-heads.net/avatar/${p}/24`" alt="" referrerpolicy="no-referrer" />{{ p }}<span v-if="pings[p] != null" class="pms" :class="pingClass(pings[p])" :title="$t('Opóźnienie połączenia, odświeżane co 30 s')">{{ pings[p] }} ms</span></span>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h3>{{ $t('Informacje') }}</h3></div>
          <dl class="info">
            <dt>{{ $t('Adres') }}</dt><dd class="mono">{{ $addr(s.address) }}</dd>
            <dt>{{ $t('Silnik') }}</dt><dd>{{ ENGINE_META[s.type]?.name }} {{ s.mc_version }}</dd>
            <dt v-if="s.loader_version">{{ $t('Loader / build') }}</dt><dd v-if="s.loader_version" class="mono">{{ s.loader_version }}</dd>
            <dt>Java</dt><dd>{{ s.java_feature ? `Temurin ${s.java_feature}` : $t('wymagana {j}+', { j: s.required_java || '?' }) }}</dd>
            <dt>{{ $t('Tryb online') }}</dt><dd>{{ s.online_mode ? $t('tak (konta premium)') : $t('nie (offline/cracked)') }}</dd>
            <dt>{{ $t('Whitelista') }}</dt><dd>{{ s.whitelist ? $t('włączona') : $t('wyłączona') }}</dd>
            <dt>{{ $t('Port wewnętrzny') }}</dt><dd class="mono">{{ s.internal_port }}</dd>
            <dt>{{ $t('Rozmiar na dysku') }}</dt><dd class="num">{{ disk != null ? formatBytes(disk) : '…' }}</dd>
          </dl>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import { Timer, Users, Cpu, MemoryStick, ArrowRight } from 'lucide-vue-next'
import AreaChart from '../../components/AreaChart.vue'
import Meter from '../../components/Meter.vue'
import { api } from '../../api'
import { usePlayerPings, pingClass } from '../../pings'
import { ENGINE_META, STATE, ansiToHtml, formatBytes, formatDuration, formatMB } from '../../utils'

const live = inject('live')
const s = computed(() => live.summary)
const { pings } = usePlayerPings(() => s.value.id, () => s.value.players)
const running = computed(() => ['online', 'starting', 'stopping'].includes(s.value.state))
const now = ref(Date.now() / 1000)
const disk = ref(null)
const cpuCount = ref(1)
const windowSec = 900
const windowLabel = '15 min'
const stateText = computed(() => STATE[s.value.state]?.label || s.value.state)
const cpuPts = computed(() => live.stats.map((p) => ({ t: p.t, v: p.cpu })))
const memPts = computed(() => live.stats.map((p) => ({ t: p.t, v: p.mem })))
const tail = computed(() => live.console.slice(-14))
let t
onMounted(async () => {
  t = setInterval(() => (now.value = Date.now() / 1000), 1000)
  try { const st = await api.get(`/servers/${s.value.id}/stats`); disk.value = st.disk; cpuCount.value = st.cpu_count } catch {}
})
onUnmounted(() => clearInterval(t))
</script>
<style scoped>
.tile { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.tl { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-2); font-weight: 550; }
.tl svg { width: 15px; height: 15px; color: var(--muted); }
.tv { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.tv small { font-size: 13px; color: var(--muted); font-weight: 500; }
.tail { background: var(--console-bg); color: #cfd6d0; font-size: 12px; padding: 12px 16px; border-radius: 0 0 var(--radius-lg) var(--radius-lg); min-height: 240px; line-height: 1.6; overflow: hidden; }
.tl-line { white-space: pre-wrap; word-break: break-word; }
.tl-line.panel { color: #8fc7ec; }
.tl-line.cmd { color: #a6e07f; }
.player { display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px 4px 4px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); font-size: 13px; }
.player img { width: 22px; height: 22px; border-radius: 50%; image-rendering: pixelated; }
.pms { font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.pms.green { color: var(--accent); }
.pms.amber { color: var(--warn); }
.pms.red { color: var(--danger); }
.info { display: grid; grid-template-columns: auto 1fr; gap: 9px 16px; margin: 0; padding: 16px 20px; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
@media (max-width: 900px) { .grid[style] { grid-template-columns: 1fr !important; } }
</style>
