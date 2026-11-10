<template>
  <article class="card server" @click="$router.push(`/servers/${s.id}`)">
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
    <button class="address mono small" :title="$t('Kopiuj adres')" @click.stop="$emit('copy', s.address)">
      <Globe /> <span class="ellipsis">{{ $addr(s.address) }}</span> <Copy class="cp" />
    </button>
    <div v-if="s.job" class="col" style="gap: 6px">
      <div class="row tiny"><span class="text-2">{{ $tm(s.job.title) }}</span><span class="muted ellipsis">{{ $tm(s.job.stage) }}</span></div>
      <div class="progress" :class="{ indeterminate: s.job.progress == null }"><div :style="{ width: (s.job.progress || 0) * 100 + '%' }" /></div>
    </div>
    <div v-else class="metrics">
      <div>
        <div class="tiny muted">{{ $t('Gracze') }}</div>
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
      <span class="tiny muted">{{ s.started_at ? $t('działa {t}', { t: formatDuration(now - s.started_at) }) : s.installed ? $t('zatrzymany') : '' }}</span>
      <span class="spacer" />
      <button v-if="['offline', 'crashed'].includes(s.state)" class="btn sm" :disabled="!s.installed" @click="$emit('power', 'start')"><Play /> {{ $t('Start') }}</button>
      <button v-else-if="s.state === 'online' || s.state === 'starting'" class="btn sm" @click="$emit('power', 'stop')"><Square /> {{ $t('Stop') }}</button>
      <router-link :to="`/servers/${s.id}/console`" class="btn sm ghost"><TerminalSquare /> {{ $t('Konsola') }}</router-link>
    </div>
  </article>
</template>
<script setup>
import { Globe, Copy, Play, Square, TerminalSquare } from 'lucide-vue-next'
import Meter from './Meter.vue'
import StatusBadge from './StatusBadge.vue'
import ServerAvatar from './ServerAvatar.vue'
import { ENGINE_META, formatBytes, formatDuration, formatMB, mcToHtml } from '../utils'
defineProps({ s: Object, now: Number })
defineEmits(['power', 'copy'])
</script>
<style scoped>
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
</style>
