<template>
  <div class="public-page">
    <div v-if="loading && !data" class="public-center">
      <Loader2 class="spin" />
      <span class="muted">{{ $t('Ładowanie serwera…') }}</span>
    </div>

    <div v-else-if="!data" class="public-center error-card">
      <MapIcon />
      <h1>{{ $t('Nie znaleziono serwera') }}</h1>
      <p class="muted">{{ error || $t('Ten adres nie prowadzi do żadnego serwera.') }}</p>
      <router-link to="/" class="btn"><Home /> {{ $t('Przejdź do panelu') }}</router-link>
    </div>

    <template v-else>
      <header class="hero">
        <img v-if="iconOk && data.server.icon" class="server-icon" :src="data.server.icon" alt="" @error="iconOk = false" />
        <div v-else class="server-icon fallback">{{ initials(data.server.name) }}</div>
        <div class="grow" style="min-width: 0">
          <div class="row wrap" style="gap: 10px">
            <h1 class="server-name">{{ data.server.name }}</h1>
            <StatusBadge :state="data.server.state" />
          </div>
          <div class="row wrap hero-meta">
            <span>{{ engineName }} <span class="mono">{{ data.server.mc_version }}</span></span>
            <span v-if="data.server.loader_version" class="mono">{{ data.server.loader_version }}</span>
            <button class="addr mono" :title="$t('Kopiuj adres')" @click="copyAddress">
              <Globe /> {{ data.server.address }} <Copy />
            </button>
          </div>
          <div v-if="data.server.motd" class="motd" v-html="mcToHtml(data.server.motd)" />
        </div>
        <div class="hero-count">
          <div class="count-line"><span class="count-num num">{{ data.players.online }}</span><span class="count-max num">/ {{ data.players.max }}</span></div>
          <div class="small muted">{{ $t('graczy online') }}</div>
          <div class="server-run small" :class="{ on: data.server.running }">
            <span class="dot" />{{ data.server.running ? $t('Serwer jest włączony') : $t('Serwer jest wyłączony') }}
          </div>
        </div>
      </header>

      <div class="players-grid">
        <section class="card">
          <div class="card-head">
            <Users /><h3>{{ $t('Gracze online') }}</h3>
            <span class="badge green">{{ data.online.length }}</span>
          </div>
          <div class="card-body">
            <div v-if="!data.online.length" class="players-empty">
              <Gamepad2 />
              <span>{{ $t('Nikt teraz nie gra.') }}</span>
            </div>
            <ul v-else class="player-list">
              <li v-for="p in data.online" :key="p.name">
                <span class="avatar" :style="avatarStyle(p.name)">{{ initials(p.name) }}</span>
                <div class="grow" style="min-width: 0">
                  <b class="ellipsis">{{ p.name }}</b>
                  <div class="tiny muted">{{ $t('Na serwerze od {t}', { t: formatDuration(Math.max(0, now - (p.joined_at || now))) }) }}</div>
                </div>
                <span class="online-pill"><span class="dot" />{{ $t('online') }}</span>
              </li>
            </ul>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <UserMinus /><h3>{{ $t('Gracze offline') }}</h3>
            <span class="badge">{{ data.players.offline }}</span>
          </div>
          <div class="card-body">
            <div v-if="!data.offline.length" class="players-empty">
              <UserMinus />
              <span>{{ $t('Brak graczy w historii.') }}</span>
            </div>
            <ul v-else class="player-list offline-list">
              <li v-for="p in data.offline" :key="p.name">
                <span class="avatar dim" :style="avatarStyle(p.name)">{{ initials(p.name) }}</span>
                <div class="grow" style="min-width: 0">
                  <b class="ellipsis">{{ p.name }}</b>
                  <div class="tiny muted">{{ $t('Ostatnio online {t}', { t: timeAgo(p.last_seen) }) }}</div>
                </div>
                <span class="tiny muted nowrap" :title="$t('Ostatnio online {t}', { t: timeAgo(p.last_seen) })">{{ formatDate(p.last_seen) }}</span>
              </li>
            </ul>
          </div>
        </section>
      </div>

      <section class="card map-card">
        <div class="card-head">
          <MapIcon /><h3>{{ $t('Mapa serwera') }}</h3>
          <span v-if="data.map.available" class="badge green"><span class="dot" />{{ $t('na żywo') }}</span>
          <span v-else class="badge">{{ $t('niedostępna') }}</span>
          <a v-if="data.map.available" :href="data.map.url" target="_blank" rel="noopener" class="btn sm ghost">
            <ExternalLink /> {{ $t('Otwórz w nowej karcie') }}
          </a>
        </div>
        <div v-if="data.map.available" class="map-frame">
          <iframe :src="data.map.url" :title="$t('Mapa {name}', { name: data.server.name })" loading="lazy" referrerpolicy="no-referrer" />
        </div>
        <div v-else class="card-body">
          <div class="callout info small">
            <Info />
            <div v-if="!data.map.enabled">{{ $t('Mapa squaremap jest wyłączona na tym serwerze.') }}</div>
            <div v-else>{{ $t('Mapa pojawi się po pierwszym uruchomieniu serwera z squaremap. Wygenerowanie kafelków może zająć chwilę.') }}</div>
          </div>
        </div>
      </section>

      <footer class="public-foot">
        <span>{{ $t('Strona serwera odświeża się automatycznie co 5 sekund.') }}</span>
        <span class="spacer" />
        <span class="muted">{{ $t('MineNode') }}</span>
      </footer>
    </template>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { Copy, ExternalLink, Gamepad2, Globe, Home, Info, Loader2, Map as MapIcon, UserMinus, Users } from 'lucide-vue-next'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api'
import { t } from '../i18n'
import { copyText, ENGINE_META, formatDate, formatDuration, mcToHtml, timeAgo } from '../utils'
import { toast } from '../store'

const route = useRoute()
const data = ref(null)
const error = ref('')
const loading = ref(true)
const iconOk = ref(true)
const now = ref(Date.now() / 1000)
let timer = null

const key = computed(() => String(route.params.key || ''))
const engineName = computed(() => ENGINE_META[data.value?.server.type]?.name || data.value?.server.type || '')

const COLORS = ['#4f7bd8', '#d97757', '#4f9d7a', '#b88b3d', '#8b6fc0', '#3f94b8', '#c25d83', '#6c8c3f']

function hash(text) {
  let value = 0
  for (const char of text || '?') value = (value * 31 + char.charCodeAt(0)) >>> 0
  return value
}
function initials(name) {
  const clean = String(name || '?').replace(/[^A-Za-z0-9_]/g, '')
  return (clean.slice(0, 2) || '?').toUpperCase()
}
function avatarStyle(name) {
  return { background: COLORS[hash(name.toLowerCase()) % COLORS.length] }
}

async function load() {
  try {
    data.value = await api.get(`/public/servers/${encodeURIComponent(key.value)}/stats`)
    error.value = ''
    now.value = Date.now() / 1000
  } catch (err) {
    if (!data.value) error.value = err?.status === 404 ? t('Ten adres nie prowadzi do żadnego serwera.') : (err?.message || '')
  } finally {
    loading.value = false
  }
}

async function copyAddress() {
  if (await copyText(data.value?.server.address || '')) toast(t('Skopiowano adres'), 'success')
}

watch(key, () => { data.value = null; iconOk.value = true; loading.value = true; load() })
watchEffect(() => {
  if (data.value) document.title = `${data.value.server.name} – ${data.value.players.online} ${t('online')}`
})
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (document.visibilityState === 'visible') load()
  }, 5000)
})
onUnmounted(() => {
  clearInterval(timer)
  document.title = 'MineNode'
})
</script>
<style scoped>
.public-page { max-width: 1240px; margin: 0 auto; padding: 34px 28px 56px; }
.public-center { min-height: 70vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; text-align: center; }
.public-center > svg { width: 42px; height: 42px; color: var(--muted); opacity: 0.75; }
.public-center .btn { margin-top: 6px; }
.error-card h1 { font-size: 20px; }

.hero {
  position: relative; display: flex; align-items: center; gap: 18px; padding: 24px 26px;
  border: 1px solid var(--border); border-radius: 18px; box-shadow: var(--shadow); overflow: hidden;
  background:
    radial-gradient(900px 240px at 0% 0%, rgba(134, 192, 98, 0.17), transparent 62%),
    radial-gradient(760px 220px at 100% 110%, rgba(100, 168, 216, 0.13), transparent 60%),
    var(--surface);
}
.server-icon { width: 64px; height: 64px; border-radius: 14px; flex: none; object-fit: cover; border: 1px solid var(--border-strong); background: var(--surface-2); }
.server-icon.fallback { display: grid; place-items: center; font-weight: 700; font-size: 22px; color: var(--accent); background: var(--accent-soft); }
.server-name { font-size: 25px; overflow-wrap: anywhere; }
.hero-meta { gap: 8px 16px; margin-top: 6px; color: var(--text-2); font-size: 13px; }
.addr { display: inline-flex; align-items: center; gap: 6px; padding: 2px 0; border: 0; background: none; color: var(--accent); font: inherit; font-size: 12.5px; cursor: pointer; }
.addr svg { width: 14px; height: 14px; }
.addr:hover { text-decoration: underline; }
.motd { margin-top: 10px; color: var(--text-2); font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.hero-count { flex: none; min-width: 150px; text-align: right; }
.count-line { display: flex; align-items: baseline; justify-content: flex-end; gap: 5px; }
.count-num { font-size: 40px; line-height: 1; font-weight: 650; letter-spacing: -0.03em; }
.count-max { color: var(--muted); font-size: 16px; }
.server-run { display: flex; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 9px; color: var(--muted); }
.server-run .dot { width: 7px; height: 7px; }
.server-run.on { color: var(--accent); }
.server-run.on .dot { background: currentColor; }

.players-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
.players-grid .card-head > svg, .map-card .card-head > svg { width: 17px; height: 17px; color: var(--muted); }
.players-grid .card-body { padding-top: 6px; padding-bottom: 6px; }
.player-list { list-style: none; margin: 0; padding: 0; }
.player-list li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.player-list li:last-child { border-bottom: 0; }
.offline-list { max-height: 360px; overflow-y: auto; padding-right: 4px; }
.avatar { width: 36px; height: 36px; border-radius: 9px; display: grid; place-items: center; flex: none; color: #fff; font-size: 12px; font-weight: 700; letter-spacing: 0.02em; }
.avatar.dim { filter: saturate(0.45); opacity: 0.75; }
.online-pill { display: inline-flex; align-items: center; gap: 6px; color: var(--accent); font-size: 12px; white-space: nowrap; }
.online-pill .dot { width: 7px; height: 7px; background: currentColor; }
.players-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 32px 10px; color: var(--muted); font-size: 13px; }
.players-empty svg { width: 26px; height: 26px; opacity: 0.65; }

.map-card { margin-top: 16px; }
.map-frame { padding: 12px; }
.map-frame iframe { display: block; width: 100%; height: min(72vh, 760px); min-height: 460px; border: 0; border-radius: 10px; background: #101512; }

.public-foot { display: flex; align-items: center; gap: 12px; margin-top: 18px; padding: 0 4px; color: var(--muted); font-size: 12px; }

@media (max-width: 900px) {
  .public-page { padding: 20px 16px 40px; }
  .players-grid { grid-template-columns: 1fr; }
  .hero { flex-wrap: wrap; padding: 20px; }
  .hero-count { width: 100%; text-align: left; }
  .count-line, .server-run { justify-content: flex-start; }
}
</style>
