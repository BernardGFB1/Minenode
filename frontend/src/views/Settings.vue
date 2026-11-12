<template>
  <div class="page" style="max-width: 980px">
    <div class="page-head"><div class="grow"><h1>{{ $t('Ustawienia panelu') }}</h1><div class="sub">{{ $t('Domena, proxy, integracje, środowiska Java i konta.') }}</div></div></div>

    <div v-if="s" class="col" style="gap: 16px">
      <section class="card">
        <div class="card-head"><Network /><h3>{{ $t('Domena i reverse proxy') }}</h3></div>
        <div class="card-body col" style="gap: 16px">
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Domena bazowa') }}</label>
              <input v-model="s.base_domain" class="input mono" :placeholder="$t('mc.example.com')" />
              <span class="hint">{{ $t('Serwery będą dostępne jako') }} <span class="mono">{{ $t('subdomena') }}.{{ s.base_domain || 'mc.example.com' }}</span></span>
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
              <span class="mono">*.{{ s.base_domain || 'mc.example.com' }} → A → {{ $t('IP serwera') }}</span>
              {{ $t('(wildcard), aby każda subdomena trafiała do panelu. Proxy rozpoznaje serwer po nazwie hosta wpisanej przez gracza.') }}
            </div>
          </div>
          <div class="row"><span class="spacer" /><button class="btn primary" :disabled="saving" @click="save"><Save /> {{ $t('Zapisz') }}</button></div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><LockKeyhole /><h3>{{ $t('Autoryzacja graczy') }}</h3>
          <span v-if="s.auth_lobby?.running" class="badge green">{{ $t('lobby działa') }}</span>
          <span v-else class="badge amber">{{ $t('lobby uruchamiane') }}</span>
        </div>
        <div class="card-body col" style="gap: 12px">
          <Toggle v-model="s.player_auth_enabled" :label="$t('Wymagaj logowania na serwerach offline-mode')"
                  :hint="$t('Gracz bez sesji trafi do ukrytego lobby Paper, gdzie użyje /register lub /login. Serwery online-mode korzystają z autoryzacji Mojang.')" />
          <div class="callout info small"><Info /><div>
            {{ $t('Sesja trwa 30 minut od ostatniego wejścia lub wyjścia i jest przypisana do nicku oraz adresu IP.') }}
            <span v-if="s.auth_lobby?.port"> {{ $t('Port wewnętrzny lobby:') }} <span class="mono">{{ s.auth_lobby.port }}</span>.</span>
          </div></div>
          <div v-if="s.auth_lobby?.error" class="callout warn small"><Info />{{ s.auth_lobby.error }}</div>
          <div class="row"><span class="spacer" /><button class="btn primary" :disabled="saving" @click="saveAuth"><Save /> {{ $t('Zapisz autoryzację') }}</button></div>
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
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Network, Info, Save, KeyRound, LockKeyhole, Coffee, Users, Trash2, UserPlus, Plus, Loader2, MonitorSmartphone, RotateCcw } from 'lucide-vue-next'
import MotdEditor from '../components/MotdEditor.vue'
import BackupDestinations from '../components/BackupDestinations.vue'
import Toggle from '../components/Toggle.vue'
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
const saving = ref(false)
const cfKey = ref('')
const runtimes = ref([])
const javaAvail = ref([])
const javaBusy = ref(null)
const users = ref([])
const nu = reactive({ username: '', password: '' })
const pw = reactive({ user: null, value: '' })
const installed = computed(() => new Set(runtimes.value.map((r) => r.feature)))
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

async function save() {
  saving.value = true
  try {
    s.value = await api.put('/settings', { base_domain: s.value.base_domain, default_server_id: s.value.default_server_id || '', public_address: s.value.public_address, public_panel_url: s.value.public_panel_url || '', pack_subdomain: s.value.pack_subdomain || 'packs', bedrock_advertise: s.value.bedrock_advertise || '' })
    toast('Zapisano ustawienia', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
}
async function saveKey() {
  try { s.value = await api.put('/settings', { curseforge_api_key: cfKey.value }); cfKey.value = ''; toast('Zapisano klucz CurseForge', 'success') } catch (e) { toastError(e) }
}
async function saveAuth() {
  saving.value = true
  try {
    s.value = await api.put('/settings', { player_auth_enabled: s.value.player_auth_enabled })
    toast('Zapisano ustawienia autoryzacji', 'success')
  } catch (e) { toastError(e) } finally { saving.value = false }
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
</style>
