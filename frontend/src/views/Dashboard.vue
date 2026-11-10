<template>
  <div class="page">
    <div class="page-head">
      <div class="grow">
        <h1>Serwery</h1>
        <div class="sub">{{ fleet.servers.length }} skonfigurowanych · {{ sys?.servers_online ?? 0 }} online · {{ sys?.players_online ?? 0 }} graczy</div>
      </div>
      <router-link to="/modpacks" class="btn"><Package /> Importuj modpack</router-link>
      <router-link to="/new" class="btn primary"><Plus /> Nowy serwer</router-link>
    </div>

    <div class="grid grid-4" style="margin-bottom: 22px">
      <div class="card stat">
        <div class="stat-label"><Cpu /> Procesor</div>
        <div class="stat-value num">{{ sys ? Math.round(sys.cpu_percent) : '–' }}<small>%</small></div>
        <Meter :value="(sys?.cpu_percent || 0) / 100" />
        <div class="tiny muted">{{ sys?.cpu_count }} rdzeni · {{ sys?.platform }}</div>
      </div>
      <div class="card stat">
        <div class="stat-label"><MemoryStick /> Pamięć</div>
        <div class="stat-value num">{{ sys ? formatBytes(sys.mem_used) : '–' }}</div>
        <Meter :value="sys ? sys.mem_used / sys.mem_host_total : 0" />
        <div class="tiny muted">z {{ sys ? formatBytes(sys.mem_total) : '–' }}{{ sys?.container_limit ? ' (limit kontenera)' : '' }}</div>
      </div>
      <div class="card stat">
        <div class="stat-label"><HardDrive /> Dysk danych</div>
        <div class="stat-value num">{{ sys ? formatBytes(sys.disk_used) : '–' }}</div>
        <Meter :value="sys ? sys.disk_used / sys.disk_total : 0" />
        <div class="tiny muted">wolne {{ sys ? formatBytes(sys.disk_free) : '–' }}</div>
      </div>
      <div class="card stat">
        <div class="stat-label"><Network /> Proxy</div>
        <div class="stat-value num">{{ sys?.proxy?.active ?? 0 }}<small> połączeń</small></div>
        <div class="row tiny" :class="sys?.proxy?.listening ? 'muted' : ''" :style="!sys?.proxy?.listening && sys ? 'color: var(--danger)' : ''">
          <span class="dot" :style="{ background: sys?.proxy?.listening ? 'var(--accent)' : 'var(--danger)' }" />
          {{ sys?.proxy?.listening ? `nasłuchuje na porcie ${sys.proxy.port}` : sys?.proxy?.error || 'nie działa' }}
        </div>
        <div class="tiny muted">łącznie {{ compact(sys?.proxy?.total) }} połączeń · ↓ {{ formatBytes(sys?.proxy?.bytes_down) }}</div>
      </div>
    </div>

    <div v-if="fleet.loaded && !fleet.servers.length" class="card onboarding">
      <Boxes class="big-icon" />
      <h2>Utwórz swój pierwszy serwer</h2>
      <p class="muted">Wybierz silnik (Vanilla, Paper, Fabric, Forge…) i wersję – panel pobierze pliki serwera i właściwą Javę automatycznie.</p>
      <div class="row" style="justify-content: center; margin-top: 18px">
        <router-link to="/modpacks" class="btn"><Package /> Z modpacka</router-link>
        <router-link to="/new" class="btn primary"><Plus /> Nowy serwer</router-link>
      </div>
    </div>

    <div class="servers">
      <article v-for="s in fleet.servers" :key="s.id" class="card server" @click="$router.push(`/servers/${s.id}`)">
        <div class="row" style="gap: 12px; align-items: flex-start">
          <ServerAvatar :server="s" :size="46" />
          <div class="grow" style="min-width: 0">
            <div class="row" style="gap: 8px">
              <h3 class="ellipsis">{{ s.name }}</h3>
            </div>
            <div class="small muted ellipsis">{{ ENGINE_META[s.type]?.name }} {{ s.mc_version !== '?' ? s.mc_version : '' }}<template v-if="s.modpack"> · {{ s.modpack.name }}</template></div>
          </div>
          <StatusBadge :state="s.state" />
        </div>
        <div class="motd small" v-html="mcToHtml(s.motd)" />
        <button class="address mono small" title="Kopiuj adres" @click.stop="copy(s.address)">
          <Globe /> <span class="ellipsis">{{ s.address }}</span> <Copy class="cp" />
        </button>
        <div v-if="s.job" class="col" style="gap: 6px">
          <div class="row tiny"><span class="text-2">{{ s.job.title }}</span><span class="muted ellipsis">{{ s.job.stage }}</span></div>
          <div class="progress" :class="{ indeterminate: s.job.progress == null }"><div :style="{ width: (s.job.progress || 0) * 100 + '%' }" /></div>
        </div>
        <div v-else class="metrics">
          <div>
            <div class="tiny muted">Gracze</div>
            <div class="num"><b>{{ s.players_online }}</b><span class="muted">/{{ s.max_players }}</span></div>
          </div>
          <div>
            <div class="tiny muted">CPU</div>
            <div class="num">{{ s.state === 'online' || s.state === 'starting' ? Math.round(s.cpu) + '%' : '–' }}</div>
          </div>
          <div class="grow">
            <div class="row tiny"><span class="muted">RAM</span><span class="spacer" /><span class="num text-2">{{ s.mem ? formatBytes(s.mem) : '–' }} / {{ formatMB(s.max_ram_mb) }}</span></div>
            <Meter :value="s.mem / (s.max_ram_mb * 1048576 * 1.15)" style="margin-top: 6px" />
          </div>
        </div>
        <div class="foot" @click.stop>
          <span class="tiny muted">{{ s.started_at ? 'działa ' + formatDuration(now - s.started_at) : s.installed ? 'zatrzymany' : '' }}</span>
          <span class="spacer" />
          <button v-if="['offline', 'crashed'].includes(s.state)" class="btn sm" :disabled="!s.installed" @click="power(s, 'start')"><Play /> Start</button>
          <button v-else-if="s.state === 'online' || s.state === 'starting'" class="btn sm" @click="power(s, 'stop')"><Square /> Stop</button>
          <router-link :to="`/servers/${s.id}/console`" class="btn sm ghost"><TerminalSquare /> Konsola</router-link>
        </div>
      </article>
    </div>
  </div>
</template>
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Plus, Package, Cpu, MemoryStick, HardDrive, Network, Boxes, Globe, Copy, Play, Square, TerminalSquare } from 'lucide-vue-next'
import Meter from '../components/Meter.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ServerAvatar from '../components/ServerAvatar.vue'
import { api } from '../api'
import { fleet, refreshFleet } from '../servers'
import { toast, toastError } from '../store'
import { ENGINE_META, compact, copyText, formatBytes, formatDuration, formatMB, mcToHtml } from '../utils'

const sys = ref(null)
const now = ref(Date.now() / 1000)
let t
async function loadSys() { try { sys.value = await api.get('/system') } catch {} }
onMounted(() => {
  loadSys()
  t = setInterval(() => { now.value = Date.now() / 1000; if (document.visibilityState === 'visible') loadSys() }, 5000)
})
onUnmounted(() => clearInterval(t))

async function power(s, action) {
  try { await api.post(`/servers/${s.id}/power`, { action }); refreshFleet() } catch (e) { toastError(e) }
}
async function copy(text) { if (await copyText(text)) toast('Skopiowano adres', 'success', 1800) }
</script>
<style scoped>
.stat { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.stat-label { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-2); font-weight: 550; }
.stat-label svg { width: 15px; height: 15px; color: var(--muted); }
.stat-value { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.stat-value small { font-size: 13px; color: var(--muted); font-weight: 500; }
.servers { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.server { padding: 18px; display: flex; flex-direction: column; gap: 14px; cursor: pointer; transition: border-color 0.15s, transform 0.15s; }
.server:hover { border-color: var(--border-strong); }
.motd { background: var(--console-bg); color: #aaa; border-radius: var(--radius-sm); padding: 8px 10px; font-family: var(--mono); font-size: 12px; min-height: 34px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.address { display: flex; align-items: center; gap: 8px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; color: var(--text-2); cursor: pointer; text-align: left; font-family: var(--mono); }
.address:hover { border-color: var(--border-strong); color: var(--text); }
.address svg { width: 14px; height: 14px; flex: none; color: var(--muted); }
.address .cp { margin-left: auto; opacity: 0; }
.address:hover .cp { opacity: 1; }
.metrics { display: flex; gap: 22px; align-items: flex-end; }
.foot { display: flex; align-items: center; gap: 6px; border-top: 1px solid var(--border); padding-top: 12px; margin-top: auto; cursor: default; }
.onboarding { padding: 48px 24px; text-align: center; margin-bottom: 20px; }
.onboarding p { max-width: 480px; margin: 8px auto 0; }
.big-icon { width: 36px; height: 36px; color: var(--accent); margin-bottom: 10px; }
</style>
