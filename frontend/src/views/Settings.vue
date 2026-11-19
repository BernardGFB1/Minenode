<template>
  <div class="page" style="max-width: 980px">
    <div class="page-head"><div class="grow"><h1>{{ $t('Ustawienia panelu') }}</h1><div class="sub">{{ $t('Domena, proxy, integracje, środowiska Java i konta.') }}</div></div></div>

    <div v-if="s" class="col" style="gap: 16px">
      <section class="card">
        <div class="card-head"><Network /><h3>{{ $t('Domena i reverse proxy') }}</h3></div>
        <div class="card-body col" style="gap: 16px">
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Domeny główne') }}</label>
              <div class="col" style="gap: 6px">
                <div v-for="(d, i) in domainList" :key="i" class="input-group">
                  <input v-model="domainList[i]" class="input mono" :placeholder="i === 0 ? $t('mc.example.com') : $t('np. mc.druga-domena.pl')" />
                  <span v-if="i === 0" class="addon tiny">{{ $t('domyślna') }}</span>
                  <button v-else class="btn addon-btn" :title="$t('Usuń domenę')" @click="domainList.splice(i, 1)"><Trash2 /></button>
                </div>
                <button type="button" class="btn sm ghost" style="align-self: flex-start" :disabled="domainList.length >= 50" @click="domainList.push('')"><Plus /> {{ $t('Dodaj domenę') }}</button>
              </div>
              <span class="hint">{{ $t('Każdy serwer działa pod') }} <span class="mono">{{ $t('subdomena') }}.{{ domainList[0] || 'mc.example.com' }}</span><template v-if="extraCount">
                {{ $t('oraz pod {n} kolejnymi domenami. Pierwsza jest domyślna – w ustawieniach serwera możesz wybrać, którą pokazywać graczom.', { n: extraCount }) }}</template></span>
            </div>
            <div class="field">
              <label>{{ $t('Serwer domyślny') }}</label>
              <select v-model="s.default_server_id" class="select">
                <option :value="null">{{ $t('— brak (odrzuć nieznane hosty) —') }}</option>
                <option v-for="sv in fleet.servers" :key="sv.id" :value="sv.id">{{ sv.name }}</option>
              </select>
              <span class="hint">{{ $t('Dla połączeń po samym IP lub nieznanej subdomenie.') }}</span>
            </div>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Publiczny adres (opcjonalnie)') }}</label>
              <input v-model="s.public_address" class="input mono" :placeholder="$t('np. 203.0.113.10')" />
              <span class="hint">{{ $t('Pokazywany w adresach serwerów, gdy nie ustawiono domeny.') }}</span>
            </div>
            <div class="field">
              <label>{{ $t('Subdomena paczek zasobów') }}</label>
              <div class="input-group">
                <input v-model="s.pack_subdomain" class="input mono" :placeholder="$t('packs')" />
                <span class="addon">.{{ s.base_domain || $t('domena') }}:{{ s.proxy_port }}</span>
              </div>
              <span class="hint">{{ $t('Paczki zasobów są wysyłane graczom przez port Minecrafta – nie trzeba otwierać dodatkowego portu.') }}</span>
            </div>
          </div>
          <details class="adv">
            <summary class="small text-2">{{ $t('Własny adres hostingu paczek (zaawansowane)') }}</summary>
            <div class="field" style="margin-top: 10px; max-width: 50%">
              <input v-model="s.public_panel_url" class="input mono" :placeholder="$t('https://packs.example.com')" />
              <span class="hint">{{ $t('Zostaw puste, aby używać portu Minecrafta. Wpisz adres tylko wtedy, gdy serwujesz paczki przez własny reverse proxy HTTP(S).') }}</span>
            </div>
          </details>
          <details class="adv">
            <summary class="small text-2">{{ $t('Adres ogłaszany graczom Bedrock (zaawansowane)') }}</summary>
            <div class="field" style="margin-top: 10px; max-width: 50%">
              <input v-model="s.bedrock_advertise" class="input mono" :placeholder="s.public_address || $t('np. 203.0.113.10 lub mc.example.com:19132')" />
              <span class="hint">{{ $t('Adres, pod który gra Bedrock łączy się po znalezieniu serwera. Zostaw puste, aby użyć publicznego adresu powyżej (albo adresu serwera w domenie). Kilka adresów oddziel przecinkami; port jest opcjonalny.') }}</span>
            </div>
          </details>
          <div class="small text-2">{{ $t('Adres paczek:') }} <span class="mono">{{ s.resource_pack_base || '— ' + $t('ustaw domenę bazową lub publiczny adres') + ' —' }}</span></div>
          <div class="callout info small">
            <Info />
            <div>
              {{ $t('Proxy nasłuchuje na porcie') }} <b class="mono">{{ s.proxy_port }}</b> {{ $t('(TCP), a czat głosowy na') }} <b class="mono">{{ s.voice_port }}</b> {{ $t('(UDP). Dodaj w DNS rekord') }}
              <span v-for="(d, i) in (cleanDomains.length ? cleanDomains : ['mc.example.com'])" :key="d" class="mono">{{ i ? ', ' : '' }}*.{{ d }} → A → {{ $t('IP serwera') }}</span>
              {{ $t('(wildcard), aby każda subdomena trafiała do panelu. Proxy rozpoznaje serwer po nazwie hosta wpisanej przez gracza.') }}
            </div>
          </div>
          <div class="row"><span class="spacer" /><button class="btn primary" :disabled="saving" @click="save"><Save /> {{ $t('Zapisz') }}</button></div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <ScrollText /><h3>{{ $t('Logi Geysera (Bedrock)') }}</h3>
          <span v-if="geyser.error" class="badge red">{{ $t('błąd') }}</span>
          <span v-else-if="geyser.listening" class="badge green"><span class="dot" />{{ $t('bramka działa') }}</span>
          <span v-else-if="geyser.enabled" class="badge amber">{{ $t('Uruchamianie…') }}</span>
          <span v-else class="badge">{{ $t('wyłączony') }}</span>
        </div>
        <div class="card-body col" style="gap: 12px">
          <div class="row wrap" style="gap: 8px">
            <span class="small text-2 grow">{{ $t('Podgląd pracy bramki Bedrock – diagnostyka połączeń i NetherNet.') }}</span>
            <button class="btn sm" :disabled="geyserBusy" @click="loadGeyserLogs(true)"><RefreshCw /> {{ $t('Odśwież') }}</button>
            <button class="btn sm danger" :disabled="!geyser.entries.length || geyserBusy" @click="clearGeyserLogs"><Eraser /> {{ $t('Wyczyść logi') }}</button>
          </div>
          <div v-if="geyser.error" class="callout warn small"><AlertTriangle /><div>{{ geyser.error }}</div></div>
          <div ref="geyserLogEl" class="geyser-log mono" @scroll="onGeyserScroll">
            <div v-for="e in geyser.entries" :key="e.seq" class="gl" :class="geyserLevel(e.text)">
              <span class="ts">{{ geyserTime(e.t) }}</span>
              <span class="tx">{{ e.text }}</span>
            </div>
            <div v-if="!geyser.entries.length" class="empty">{{ geyser.enabled ? $t('Brak logów.') : $t('Brak logów – bramka Bedrock nie jest uruchomiona.') }}</div>
          </div>
          <form class="row" style="gap: 8px" @submit.prevent="sendGeyserCommand">
            <div class="cmd grow"><span class="mono muted">&gt;</span><input ref="geyserCmdEl" v-model="geyserCmd" class="mono" maxlength="200" :disabled="!geyser.running" :placeholder="$t('Komenda Geysera, np. geyser list')" /></div>
            <button type="button" class="btn sm" :disabled="!geyser.running" :title="$t('Okno przesyłu, RTT i przekroczenia czasu połączenia NetherNet gracza (tylko wersja testowa)')" @click="statsPreset"><Activity /> {{ $t('Statystyki NetherNet') }}</button>
            <button class="btn sm" :disabled="!geyser.running || !geyserCmd.trim() || cmdBusy"><Send /> {{ $t('Wyślij') }}</button>
          </form>
          <div class="channel col" style="gap: 10px">
            <div class="row wrap" style="gap: 10px">
              <b class="small">{{ $t('Wersja Geysera') }}</b>
              <div class="seg">
                <button type="button" :class="{ active: channel.channel === 'stable' }" :disabled="channelBusy" @click="setChannel('stable')">{{ $t('Stabilna') }}</button>
                <button type="button" :class="{ active: channel.channel === 'preview' }" :disabled="channelBusy" @click="setChannel('preview')">{{ $t('Testowa (PR)') }}</button>
              </div>
              <template v-if="channel.channel === 'preview'">
                <span class="small text-2">PR #</span>
                <input v-model="channel.pr" class="input mono pr" inputmode="numeric" maxlength="6" @keydown.enter.prevent="setChannel('preview')" />
                <button type="button" class="btn sm" :disabled="channelBusy || channel.pr === currentPr" @click="setChannel('preview')">{{ $t('Zastosuj') }}</button>
              </template>
              <Loader2 v-if="channelBusy" class="spin" style="width: 16px" />
            </div>
            <Toggle :model-value="geyser.integrated_pack" :disabled="channelBusy" :label="$t('Paczka zasobów Geysera dla graczy Bedrock')"
                    :hint="$t('Geyser wysyła ją każdemu graczowi Bedrock (m.in. animacje i elementy, których Bedrock nie ma). Telefon przetwarza ją przy każdym wejściu – wyłącz na próbę, jeśli wczytywanie trwa bardzo długo. Zmiana restartuje tylko bramkę Bedrock.')"
                    @update:model-value="setIntegratedPack" />
            <div v-if="channel.channel === 'preview'" class="callout warn small"><FlaskConical /><div>
              {{ $t('Wersja testowa pochodzi z otwartego zgłoszenia zmian w Geyserze i nie jest oficjalnym wydaniem – może mieć błędy. PR #6873 zawiera poprawki i diagnostykę połączeń NetherNet (wolne wczytywanie, opóźnienia) oraz komendę „geyser nethernet stats”. Przełączenie restartuje tylko bramkę Bedrock.') }}
              <a :href="`https://github.com/GeyserMC/Geyser/pull/${channel.pr || 6873}`" target="_blank" rel="noopener">{{ $t('Szczegóły PR') }}</a>
            </div></div>
          </div>
          <div class="tiny muted">
            {{ $t('Logi odświeżają się automatycznie co 3 sekundy.') }}
            <span v-if="geyser.version"> · Geyser {{ geyser.version }}<template v-if="geyser.installed_preview"> ({{ $t('testowa') }})</template></span>
            <span v-if="geyser.port"> · {{ $t('Port') }} <span class="mono">{{ geyser.port }}</span> TCP + UDP</span>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><MonitorSmartphone /><h3>{{ $t('Wygląd wyłączonych serwerów') }}</h3>
          <span class="small muted">{{ $t('to widzi gracz, gdy serwer nie działa') }}</span></div>
        <div class="card-body col" style="gap: 22px">
          <div class="callout info small"><Info /><div>{{ $t('Proxy odpowiada za serwery, które są wyłączone lub się uruchamiają. Dostępne zmienne:') }}
            <span v-for="p in PH" :key="p.key" class="mono ph" :title="$t(p.label)">{{ '{' + p.key + '}' }}</span> {{ $t('– np.') }} <span class="mono">{{ '{join_hint}' }}</span> {{ $t('dopisze „dołącz, aby uruchomić”, gdy serwer ma włączony start przy dołączeniu.') }}</div></div>
          <div v-for="b in BLOCKS" :key="b.id" class="motd-block">
            <div class="row"><h3 class="grow">{{ $t(b.title) }}</h3>
              <button class="btn sm ghost" :disabled="s[b.motd] === s.defaults[b.motd] && s[b.version] === s.defaults[b.version] && s[b.kick] === s.defaults[b.kick]" @click="resetBlock(b)"><RotateCcw /> {{ $t('Domyślne') }}</button></div>
            <div class="grid motd-grid">
              <div class="col" style="gap: 14px">
                <div class="field"><label>{{ $t('MOTD na liście serwerów') }}</label>
                  <MotdEditor v-model="s[b.motd]" :max-lines="2" :placeholders="PH" compact /></div>
                <div class="field"><label>{{ $t('Tekst zamiast liczby graczy (wersja)') }}</label>
                  <MotdEditor v-model="s[b.version]" :max-lines="1" :placeholders="[]" compact /></div>
                <div class="field"><label>{{ $t(b.kickLabel) }}</label>
                  <MotdEditor v-model="s[b.kick]" :max-lines="3" :placeholders="PH" compact /></div>
                <div v-if="b.wake" class="field"><label>{{ $t('Komunikat przy wybudzaniu (start przy dołączeniu)') }}</label>
                  <MotdEditor v-model="s.kick_waking" :max-lines="3" :placeholders="PH" compact /></div>
              </div>
              <div class="col" style="gap: 10px">
                <div class="section-title" style="margin: 0">{{ $t('Podgląd') }}</div>
                <ServerListEntry :name="sample.name" :motd="fill(s[b.motd])" :version-label="fill(s[b.version])" :icon="sample.icon" />
                <div class="kick" :style="{ fontFamily: MC_FONT }">
                  <div class="kick-title">{{ $t('Nie udało się połączyć z serwerem') }}</div>
                  <div class="kick-msg" v-html="mcToHtml(fill(s[b.kick]))" />
                </div>
              </div>
            </div>
          </div>
          <div class="row"><span class="spacer" /><button class="btn primary" :disabled="saving" @click="saveMotd"><Save /> {{ $t('Zapisz wygląd') }}</button></div>
        </div>
      </section>

      <BackupDestinations />

      <section class="card">
        <div class="card-head"><KeyRound /><h3>CurseForge</h3>
          <span v-if="s.curseforge_configured" class="badge green">{{ $t('skonfigurowano') }}{{ s.curseforge_from_env ? ' (' + $t('zmienna środowiskowa') + ')' : '' }}</span>
          <span v-else class="badge">{{ $t('brak klucza') }}</span>
        </div>
        <div class="card-body col" style="gap: 12px">
          <p class="small text-2">{{ $t('Import paczek z CurseForge wymaga własnego klucza API (bezpłatny, z') }} <a href="https://console.curseforge.com/" target="_blank" rel="noopener">{{ $t('console.curseforge.com') }}</a>{{ $t('). Modrinth działa bez klucza.') }}</p>
          <div class="row">
            <input v-model="cfKey" class="input mono grow" type="password" placeholder="$2a$10$…" autocomplete="off" />
            <button class="btn primary" :disabled="!cfKey" @click="saveKey">{{ $t('Zapisz klucz') }}</button>
            <button v-if="s.curseforge_configured && !s.curseforge_from_env" class="btn danger" @click="cfKey = '-'; saveKey()">{{ $t('Usuń') }}</button>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><Coffee /><h3>{{ $t('Środowiska Java') }}</h3>
          <div class="row" style="gap: 6px">
            <button v-for="v in javaAvail" :key="v" class="btn sm" :disabled="javaBusy === v || installed.has(v)" @click="installJava(v)">
              <Loader2 v-if="javaBusy === v" class="spin" /><Plus v-else /> Java {{ v }}
            </button>
          </div>
        </div>
        <table class="table">
          <thead><tr><th>{{ $t('Wersja') }}</th><th>{{ $t('Build') }}</th><th>{{ $t('Rozmiar') }}</th><th>{{ $t('Używana przez') }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="r in runtimes" :key="r.feature">
              <td><b>Java {{ r.feature }}</b> <span class="muted small">{{ $t('Temurin') }}</span></td>
              <td class="mono small">{{ r.release }}</td>
              <td class="num small">{{ formatBytes(r.size) }}</td>
              <td class="small">{{ r.used_by.join(', ') || '—' }}</td>
              <td style="text-align: right"><button class="btn sm ghost icon" :title="$t('Usuń')" @click="removeJava(r.feature)"><Trash2 /></button></td>
            </tr>
            <tr v-if="!runtimes.length"><td colspan="5" class="muted small">{{ $t('Żadna Java nie jest jeszcze pobrana – zostanie pobrana automatycznie przy instalacji serwera.') }}</td></tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <div class="card-head"><Users /><h3>{{ $t('Użytkownicy panelu') }}</h3></div>
        <table class="table">
          <tbody>
            <tr v-for="u in users" :key="u.username">
              <td><b>{{ u.username }}</b> <span v-if="u.username === auth.user" class="badge green">{{ $t('to Ty') }}</span></td>
              <td class="small muted">{{ $t('od {who}', { who: formatDate(u.created_at) }) }}</td>
              <td style="text-align: right">
                <button class="btn sm" @click="changePw(u.username)"><KeyRound /> {{ $t('Zmień hasło') }}</button>
                <button v-if="u.username !== auth.user" class="btn sm ghost icon" @click="removeUser(u.username)"><Trash2 /></button>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="card-body row wrap" style="border-top: 1px solid var(--border)">
          <input v-model="nu.username" class="input" style="max-width: 220px" :placeholder="$t('Nazwa użytkownika')" />
          <input v-model="nu.password" class="input" style="max-width: 220px" type="password" :placeholder="$t('Hasło (min. 8 znaków)')" autocomplete="new-password" />
          <button class="btn" :disabled="!nu.username || nu.password.length < 8" @click="addUser"><UserPlus /> {{ $t('Dodaj użytkownika') }}</button>
        </div>
      </section>
    </div>

    <Modal :open="!!pw.user" :title="$t('Nowe hasło – {user}', { user: pw.user })" width="420px" @close="pw.user = null">
      <input v-model="pw.value" class="input" type="password" :placeholder="$t('Min. 8 znaków')" autocomplete="new-password" @keydown.enter="savePw" />
      <template #footer><button class="btn" @click="pw.user = null">{{ $t('Anuluj') }}</button><button class="btn primary" :disabled="pw.value.length < 8" @click="savePw">{{ $t('Zmień') }}</button></template>
    </Modal>
  </div>
</template>
<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Network, Info, Save, KeyRound, Coffee, Users, Trash2, UserPlus, Plus, Loader2, MonitorSmartphone, RotateCcw, ScrollText, RefreshCw, Eraser, AlertTriangle, Activity, Send, FlaskConical } from 'lucide-vue-next'
import MotdEditor from '../components/MotdEditor.vue'
import Toggle from '../components/Toggle.vue'
import BackupDestinations from '../components/BackupDestinations.vue'
import ServerListEntry from '../components/ServerListEntry.vue'
import { MC_FONT } from '../mc'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { auth, confirmDialog, toast, toastError } from '../store'
import { fleet, useFleet } from '../servers'
import { formatBytes, formatDate, mcToHtml } from '../utils'

const release = useFleet()
onUnmounted(release)
const s = ref(null)
// Editable list of main domains: [base_domain, ...extra_domains]
const domainList = ref([''])
const cleanDomains = computed(() => [...new Set(domainList.value.map((d) => d.trim().toLowerCase().replace(/^\*\./, '').replace(/^\.+|\.+$/g, '')).filter(Boolean))])
const extraCount = computed(() => Math.max(0, cleanDomains.value.length - 1))
watch(s, (v) => { if (v) domainList.value = [v.base_domain || '', ...(v.extra_domains || [])] })
const saving = ref(false)
const cfKey = ref('')
const runtimes = ref([])
const javaAvail = ref([])
const javaBusy = ref(null)
const users = ref([])
const nu = reactive({ username: '', password: '' })
const pw = reactive({ user: null, value: '' })
const installed = computed(() => new Set(runtimes.value.map((r) => r.feature)))
const geyser = reactive({ entries: [], epoch: 0, enabled: false, running: false, listening: false, error: null, version: '', port: null, installed_preview: false, integrated_pack: true })
const geyserLogEl = ref(null)
let geyserTimer = null
let geyserAfter = 0
const geyserBusy = ref(false)
const geyserCmd = ref('')
const geyserCmdEl = ref(null)
const cmdBusy = ref(false)
const channel = reactive({ channel: 'stable', pr: '6873' })
const currentPr = ref('6873')
const channelBusy = ref(false)
let channelLoaded = false
let geyserStick = true
const MOTD_KEYS = ['motd_offline', 'motd_starting', 'version_offline', 'version_starting', 'kick_offline', 'kick_starting', 'kick_waking']
const PH = [
  { key: 'name', label: 'Nazwa serwera' }, { key: 'motd', label: 'MOTD serwera z server.properties' },
  { key: 'address', label: 'Adres serwera' }, { key: 'mc_version', label: 'Wersja Minecrafta' },
  { key: 'join_hint', label: '„(dołącz, aby uruchomić)” – tylko gdy włączony start przy dołączeniu' },
]
const BLOCKS = [
  { id: 'off', title: 'Serwer wyłączony', motd: 'motd_offline', version: 'version_offline', kick: 'kick_offline', kickLabel: 'Komunikat przy próbie wejścia', wake: true },
  { id: 'start', title: 'Serwer się uruchamia', motd: 'motd_starting', version: 'version_starting', kick: 'kick_starting', kickLabel: 'Komunikat przy próbie wejścia' },
]
const sample = computed(() => {
  const sv = fleet.servers[0]
  return { name: sv?.name || 'Survival', motd: sv?.motd || '§aWitaj na serwerze!', address: sv?.address || 'survival.mc.example.com',
           mc: sv?.mc_version || '1.21.1', icon: sv?.has_icon ? `/api/servers/${sv.id}/icon` : null }
})
function fill(t) {
  return (t || '').replace(/\{name\}/g, sample.value.name).replace(/\{motd\}/g, sample.value.motd).replace(/\{address\}/g, sample.value.address)
    .replace(/\{mc_version\}/g, sample.value.mc).replace(/\{join_hint\}/g, ' §8(dołącz, aby uruchomić)')
}
function resetBlock(b) {
  for (const k of [b.motd, b.version, b.kick, ...(b.wake ? ['kick_waking'] : [])]) s.value[k] = s.value.defaults[k]
}
async function saveMotd() {
  saving.value = true
  try {
    const body = Object.fromEntries(MOTD_KEYS.map((k) => [k, s.value[k]]))
    s.value = await api.put('/settings', body)
    toast('Zapisano wygląd wyłączonych serwerów', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
}

const GEYSER_LEVEL_RE = /\/(WARN|ERROR|FATAL)\]|\[(WARN|ERROR|FATAL)\]|^\s*(WARN|ERROR)\b/
function geyserLevel(text) {
  const m = (text || '').match(GEYSER_LEVEL_RE)
  const lvl = m ? (m[1] || m[2] || m[3]).toLowerCase() : ''
  return lvl === 'fatal' ? 'error' : lvl
}
const geyserTime = (t) => new Date((t || 0) * 1000).toLocaleTimeString(undefined, { hour12: false })
function onGeyserScroll() {
  const el = geyserLogEl.value
  if (el) geyserStick = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
function applyGeyserState(r) {
  Object.assign(geyser, {
    epoch: r.epoch, enabled: r.enabled, running: r.running, listening: r.listening,
    error: r.error, version: r.version, port: r.port, installed_preview: !!r.installed_preview,
    integrated_pack: r.integrated_pack !== false,
  })
  if (!channelLoaded && r.channel) {
    channelLoaded = true
    channel.channel = r.channel
    channel.pr = currentPr.value = String(r.preview || 'pr.6873').replace(/^pr\./, '')
  }
}
async function setChannel(ch) {
  if (ch === 'preview' && !/^[0-9]{1,6}$/.test(String(channel.pr).trim())) { toast('Podaj numer PR Geysera, np. 6873', 'error'); return }
  if (ch === channel.channel && (ch === 'stable' || channel.pr === currentPr.value)) return
  if (ch === 'preview' && !(await confirmDialog({
    title: 'Przełączyć na testową wersję Geysera?',
    message: 'Panel pobierze kompilację testową z PR i zrestartuje bramkę Bedrock – gracze Bedrock zostaną na chwilę rozłączeni. Serwery Java działają dalej. Do wersji stabilnej wrócisz jednym kliknięciem.',
    confirmText: 'Przełącz',
  }))) return
  channelBusy.value = true
  try {
    const r = await api.put('/bedrock/geyser', { channel: ch, preview: ch === 'preview' ? String(channel.pr).trim() : undefined })
    channel.channel = r.channel
    currentPr.value = channel.pr = String(r.preview).replace(/^pr\./, '')
    applyGeyserState(r)
    toast(ch === 'preview' ? 'Pobieranie testowej wersji Geysera…' : 'Powrót do stabilnej wersji Geysera…', 'success')
  } catch (e) { toastError(e) } finally { channelBusy.value = false }
}
async function setIntegratedPack(on) {
  channelBusy.value = true
  try {
    applyGeyserState(await api.put('/bedrock/geyser', { integrated_pack: on }))
    toast(on ? 'Paczka Geysera włączona – bramka Bedrock się restartuje' : 'Paczka Geysera wyłączona – bramka Bedrock się restartuje', 'success')
  } catch (e) { toastError(e) } finally { channelBusy.value = false }
}
async function sendGeyserCommand() {
  const command = geyserCmd.value.trim()
  if (!command) return
  cmdBusy.value = true
  try {
    await api.post('/bedrock/command', { command })
    geyserCmd.value = ''
    geyserStick = true
    setTimeout(() => loadGeyserLogs(), 600)
  } catch (e) { toastError(e) } finally { cmdBusy.value = false }
}
function statsPreset() {
  geyserCmd.value = 'geyser nethernet stats '
  nextTick(() => geyserCmdEl.value?.focus())
}
async function loadGeyserLogs(reset = false) {
  if (geyserBusy.value) return
  geyserBusy.value = true
  try {
    const previous = geyserAfter
    let after = reset ? 0 : previous
    let r = await api.get(`/bedrock/logs?after=${after}`)
    if (after && (r.epoch !== geyser.epoch || r.next < after)) {
      // Bufor wyczyszczono lub backend wystartował ponownie – pobierz go od początku.
      r = await api.get('/bedrock/logs?after=0')
      after = 0
    }
    const replace = reset || !after || r.epoch !== geyser.epoch || r.next < previous
    geyser.entries = replace ? r.entries : [...geyser.entries, ...r.entries].slice(-800)
    applyGeyserState(r)
    geyserAfter = r.next
    if (replace) geyserStick = true
    await nextTick()
    const el = geyserLogEl.value
    if (el && geyserStick) el.scrollTop = el.scrollHeight
  } catch { /* następne odświeżenie spróbuje ponownie */ } finally { geyserBusy.value = false }
}
async function clearGeyserLogs() {
  if (!(await confirmDialog({
    title: 'Wyczyścić logi Geysera?',
    message: 'Logi znikną tylko z tego podglądu – Geyser działa dalej.',
    confirmText: 'Wyczyść',
    danger: true,
  }))) return
  try {
    const r = await api.del('/bedrock/logs')
    geyser.entries = r.entries
    applyGeyserState(r)
    geyserAfter = r.next
    toast('Wyczyszczono logi Geysera', 'success')
  } catch (e) { toastError(e) }
}

async function load() {
  try {
    const [st, j, u] = await Promise.all([api.get('/settings'), api.get('/java'), api.get('/users')])
    s.value = st
    runtimes.value = j.runtimes
    javaAvail.value = j.available
    users.value = u
  } catch (e) { toastError(e) }
}
onMounted(load)
onMounted(() => {
  loadGeyserLogs(true)
  geyserTimer = setInterval(loadGeyserLogs, 3000)
})
onUnmounted(() => clearInterval(geyserTimer))

async function save() {
  saving.value = true
  try {
    s.value = await api.put('/settings', { base_domain: cleanDomains.value[0] || '', extra_domains: cleanDomains.value.slice(1), default_server_id: s.value.default_server_id || '', public_address: s.value.public_address, public_panel_url: s.value.public_panel_url || '', pack_subdomain: s.value.pack_subdomain || 'packs', bedrock_advertise: s.value.bedrock_advertise || '' })
    toast('Zapisano ustawienia', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
}
async function saveKey() {
  try { s.value = await api.put('/settings', { curseforge_api_key: cfKey.value }); cfKey.value = ''; toast('Zapisano klucz CurseForge', 'success') } catch (e) { toastError(e) }
}
async function installJava(v) {
  javaBusy.value = v
  try { await api.post(`/java/${v}`); toast(`Pobrano Java ${v}`, 'success'); load() } catch (e) { toastError(e) } finally { javaBusy.value = null }
}
async function removeJava(v) {
  if (!(await confirmDialog({ title: `Usunąć Java ${v}?`, message: 'Zostanie pobrana ponownie, gdy będzie potrzebna.', confirmText: 'Usuń', danger: true }))) return
  try { await api.del(`/java/${v}`); load() } catch (e) { toastError(e) }
}
async function addUser() {
  try { await api.post('/users', { ...nu }); nu.username = ''; nu.password = ''; toast('Dodano użytkownika', 'success'); load() } catch (e) { toastError(e) }
}
function changePw(u) { pw.user = u; pw.value = '' }
async function savePw() {
  try { await api.put(`/users/${pw.user}/password`, { password: pw.value }); toast('Zmieniono hasło', 'success'); pw.user = null } catch (e) { toastError(e) }
}
async function removeUser(u) {
  if (!(await confirmDialog({ title: `Usunąć konto ${u}?`, confirmText: 'Usuń', danger: true }))) return
  try { await api.del(`/users/${u}`); load() } catch (e) { toastError(e) }
}
</script>
<style scoped>
.card-head > svg { width: 17px; height: 17px; color: var(--muted); }
.adv summary { cursor: pointer; }
.ph { margin: 0 3px; padding: 0 5px; border-radius: 4px; background: var(--surface-3); }
.motd-block { display: flex; flex-direction: column; gap: 12px; }
.motd-block + .motd-block { border-top: 1px solid var(--border); padding-top: 20px; }
.motd-grid { grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1000px) { .motd-grid { grid-template-columns: 1fr; } }
.kick { background: #2a201a; border-radius: 4px; padding: 18px 14px; text-align: center; color: #fff; text-shadow: 2px 2px 0 rgba(0, 0, 0, 0.55); font-size: 15px; line-height: 1.35; }
.kick-title { color: #aaa; margin-bottom: 10px; }
.kick-msg { white-space: pre-wrap; }
.card-head .badge .dot { background: currentColor; }
.geyser-log { height: 240px; overflow: auto; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--console-bg); color: #cfd6d0; font-size: 12px; line-height: 1.55; }
.geyser-log .gl { display: flex; gap: 10px; white-space: pre-wrap; word-break: break-word; }
.geyser-log .ts { flex: none; color: #56605a; user-select: none; }
.geyser-log .gl.warn .tx { color: #e3b866; }
.geyser-log .gl.error .tx { color: #ef8478; }
.geyser-log .empty { color: var(--muted); padding: 4px 0; }
.cmd { display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 10px; border-radius: var(--radius-sm); background: var(--console-bg); border: 1px solid var(--border); min-width: 180px; }
.cmd input { flex: 1; min-width: 0; background: none; border: 0; outline: none; color: #cfd6d0; font-size: 12.5px; }
.channel { padding-top: 12px; border-top: 1px solid var(--border); }
.channel .pr { width: 90px; height: 30px; }
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface-2); border: 1px solid var(--border); }
.seg button { height: 26px; padding: 0 12px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-size: 13px; font-weight: 500; cursor: pointer; }
.seg button.active { background: var(--surface-3); color: var(--text); }
.addon-btn { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; margin-left: -1px; height: auto; }
.addon-btn svg { width: 15px; height: 15px; }
</style>
