<template>
  <div class="page">
    <div v-if="!live.summary" class="empty"><Loader2 class="spin" /></div>
    <template v-else>
      <header class="head">
        <ServerAvatar :server="s" :size="56" />
        <div class="grow" style="min-width: 0">
          <div class="row" style="gap: 10px">
            <h1 class="ellipsis">{{ s.name }}</h1>
            <StatusBadge :state="s.state" />
          </div>
          <div class="row wrap meta small">
            <span>{{ ENGINE_META[s.type]?.name }} <span class="mono">{{ s.mc_version }}</span><template v-if="s.loader_version"> · <span class="mono">{{ s.loader_version }}</span></template></span>
            <span v-if="s.java_feature || s.required_java">Java {{ s.java_feature || s.required_java }}</span>
            <span v-if="s.modpack">{{ s.modpack.name }} <span class="mono">{{ s.modpack.version_name }}</span></span>
            <button class="addr mono" :title="$t('Kopiuj adres')" @click="copy"><Globe /> {{ $addr(s.address) }} <Copy /></button>
          </div>
        </div>
        <div class="actions">
          <button v-if="canStart" class="btn primary" :disabled="pending" @click="power('start')"><Play /> {{ $t('Uruchom') }}</button>
          <template v-else>
            <button class="btn" :disabled="pending || !s.installed || s.state === 'stopping' || s.state === 'installing'" @click="power('restart')"><RotateCcw /> {{ $t('Restart') }}</button>
            <button class="btn" :disabled="pending || s.state === 'stopping' || s.state === 'installing'" @click="power('stop')"><Square /> {{ $t('Stop') }}</button>
          </template>
          <button class="btn icon danger" :title="$t('Wymuś zatrzymanie (kill)')" :disabled="!['online', 'starting', 'stopping'].includes(s.state)" @click="kill"><Zap /></button>
        </div>
      </header>

      <div v-if="live.job" class="job card">
        <Loader2 class="spin" />
        <div class="grow">
          <div class="row small"><b>{{ $tm(live.job.title) }}</b><span class="muted">{{ $tm(live.job.stage) }}</span><span class="spacer" />
            <span v-if="live.job.progress != null" class="num muted">{{ Math.round(live.job.progress * 100) }}%</span></div>
          <div class="progress" :class="{ indeterminate: live.job.progress == null }" style="margin-top: 8px"><div :style="{ width: (live.job.progress || 0) * 100 + '%' }" /></div>
        </div>
      </div>
      <div v-else-if="s.state === 'install_failed'" class="callout warn" style="margin-bottom: 16px">
        <AlertTriangle /><div class="grow">{{ $t('Instalacja nie powiodła się – szczegóły w konsoli. Możesz spróbować ponownie lub zmienić wersję w') }} <router-link :to="`/servers/${id}/settings`">{{ $t('Ustawieniach') }}</router-link>.</div>
        <button class="btn sm" @click="retry">{{ $t('Ponów instalację') }}</button>
      </div>

      <nav class="tabs" style="margin-bottom: 20px">
        <router-link v-for="t in tabs" :key="t.to" :to="`/servers/${id}${t.to}`" class="tab" :exact-active-class="'active'" :active-class="t.to ? 'active' : ''">
          <component :is="t.icon" /> {{ $t(t.label) }}
          <span v-if="t.badge" class="badge amber" style="height: 18px; padding: 0 6px">{{ t.badge }}</span>
        </router-link>
      </nav>
      <router-view />
    </template>
  </div>
</template>
<script setup>
import { computed, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2, Play, Square, RotateCcw, Zap, Globe, Copy, AlertTriangle, Gauge, TerminalSquare, Users, SlidersHorizontal, Coffee, FolderOpen, Archive, Settings2, Puzzle, Image } from 'lucide-vue-next'
import ServerAvatar from '../../components/ServerAvatar.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import { api, connectServer } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { refreshFleet } from '../../servers'
import { ENGINE_META, copyText } from '../../utils'

const props = defineProps({ id: String })
const router = useRouter()
const live = reactive({ summary: null, config: null, console: [], stats: [], job: null, connected: false, pendingCount: 0, tick: 0 })
const pending = ref(false)
let conn = null
provide('live', live)
provide('reload', load)

const s = computed(() => live.summary)
const canStart = computed(() => ['offline', 'crashed', 'not_installed', 'install_failed'].includes(s.value?.state))
const tabs = computed(() => [
  { to: '', label: 'Przegląd', icon: Gauge },
  { to: '/console', label: 'Konsola', icon: TerminalSquare },
  { to: '/players', label: 'Gracze', icon: Users, badge: live.pendingCount || null },
  { to: '/game', label: 'Konfiguracja gry', icon: SlidersHorizontal },
  ...(s.value?.type !== 'vanilla' ? [{ to: '/content', label: ['paper', 'purpur'].includes(s.value?.type) ? 'Pluginy' : 'Mody', icon: Puzzle }] : []),
  { to: '/resourcepacks', label: 'Paczki zasobów', icon: Image },
  { to: '/java', label: 'Java i start', icon: Coffee },
  { to: '/files', label: 'Pliki', icon: FolderOpen },
  { to: '/backups', label: 'Kopie zapasowe', icon: Archive },
  { to: '/settings', label: 'Ustawienia', icon: Settings2 },
])

async function load() {
  try {
    const d = await api.get(`/servers/${props.id}`)
    live.config = d.config
    live.summary = d
  } catch (e) {
    if (e.status === 404) { toast('Serwer nie istnieje', 'error'); router.replace('/') } else toastError(e)
  }
}
async function loadPending() {
  try { const p = await api.get(`/servers/${props.id}/players`); live.pendingCount = p.whitelist_enabled ? p.pending.length : 0 } catch {}
}

function onMessage(m) {
  switch (m.type) {
    case 'init':
      live.summary = { ...live.summary, ...m.summary }
      live.console = m.console
      live.stats = m.stats
      live.job = m.job
      break
    case 'log':
      live.console.push(m.entry)
      if (live.console.length > 4000) live.console.splice(0, live.console.length - 4000)
      break
    case 'state':
      live.summary = { ...live.summary, ...m.summary }
      if (m.summary?.job === null) live.job = null
      refreshFleet()
      break
    case 'stats':
      live.stats.push(m.point)
      if (live.stats.length > 450) live.stats.shift()
      if (live.summary) { live.summary.cpu = m.point.cpu; live.summary.mem = m.point.mem }
      break
    case 'players':
      if (live.summary) { live.summary.players = m.players; live.summary.players_online = m.count }
      live.tick++
      break
    case 'job':
      live.job = m.job
      if (!m.job) load()
      break
    case 'attempt':
      loadPending()
      live.tick++
      break
    case 'error':
      toast(m.message, 'error')
      break
  }
}

watch(() => props.id, (id) => {
  conn?.close()
  Object.assign(live, { summary: null, config: null, console: [], stats: [], job: null, pendingCount: 0 })
  load()
  loadPending()
  conn = connectServer(id, onMessage, (st) => (live.connected = st === 'open'))
}, { immediate: true })
onUnmounted(() => conn?.close())
provide('sendCommand', (command) => conn?.send({ type: 'command', command }))

async function power(action) {
  pending.value = true
  try { live.summary = { ...live.summary, ...(await api.post(`/servers/${props.id}/power`, { action })) } } catch (e) { toastError(e) } finally { pending.value = false }
}
async function kill() {
  if (await confirmDialog({ title: 'Wymusić zatrzymanie?', message: 'Proces serwera zostanie natychmiast zabity. Niezapisane zmiany w świecie mogą zostać utracone.', confirmText: 'Zabij proces', danger: true })) power('kill')
}
async function retry() {
  try { await api.post(`/servers/${props.id}/reinstall`, {}); router.push(`/servers/${props.id}/console`) } catch (e) { toastError(e) }
}
async function copy() { if (await copyText(s.value.address)) toast('Skopiowano adres', 'success', 1800) }
</script>
<style scoped>
.head { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
.meta { color: var(--muted); gap: 6px 14px; margin-top: 4px; }
.addr { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--text-2); border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: 12px; }
.addr:hover { color: var(--text); border-color: var(--border-strong); }
.addr svg { width: 13px; height: 13px; }
.actions { display: flex; gap: 8px; }
.job { display: flex; gap: 14px; align-items: center; padding: 14px 18px; margin-bottom: 16px; }
.job > svg { width: 20px; height: 20px; color: var(--info); }
</style>
