<template>
  <div v-if="c" class="col" style="gap: 16px; max-width: 980px">
    <section class="card">
      <div class="card-head"><Tag class="hi" /><h3>{{ $t('Nazwa i adres') }}</h3></div>
      <div class="card-body col" style="gap: 16px">
        <div class="grid grid-2">
          <div class="field"><label>{{ $t('Nazwa') }}</label><input v-model="c.name" class="input" maxlength="64" /></div>
          <div class="field">
            <label>{{ $t('Subdomena') }}</label>
            <div class="input-group"><input v-model="c.subdomain" class="input mono" /><span class="addon">.{{ settings?.base_domain || 'domena' }}</span></div>
          </div>
        </div>
        <div class="field">
          <label>{{ $t('Dodatkowe nazwy hosta (aliasy)') }}</label>
          <input v-model="aliases" class="input mono" :placeholder="$t('play.mojserwer.pl, mc.przyklad.pl')" />
          <span class="hint">{{ $t('Pełne domeny kierowane do tego serwera (rozdzielone przecinkami). Ustaw dla nich rekord DNS na IP panelu.') }}</span>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><RefreshCcw class="hi" /><h3>{{ $t('Cykl życia') }}</h3></div>
      <div class="card-body grid grid-2" style="gap: 20px">
        <Toggle v-model="c.auto_start" :label="$t('Autostart')" :hint="$t('Uruchamiaj serwer razem z panelem')" />
        <Toggle v-model="c.auto_restart" :label="$t('Restart po awarii')" :hint="$t('Do 3 prób w ciągu 10 minut')" />
        <Toggle v-model="c.start_on_join" :label="$t('Start przy dołączeniu')" :hint="$t('Proxy uruchomi wyłączony serwer, gdy gracz spróbuje wejść')" />
        <div class="field">
          <label>{{ $t('Auto-wyłączanie bez graczy') }}</label>
          <select v-model.number="c.idle_shutdown_minutes" class="select" style="max-width: 260px">
            <option :value="0">{{ $t('Nigdy') }}</option>
            <option v-for="m in [5, 10, 15, 30, 60, 120, 240]" :key="m" :value="m">{{ $t('po {t} bez graczy', { t: m < 60 ? m + ' min' : m / 60 + ' h' }) }}</option>
          </select>
          <span class="hint">{{ $t('Oszczędza RAM i CPU. W połączeniu ze „Startem przy dołączeniu” serwer działa tylko, gdy ktoś gra.') }}</span>
        </div>
        <div class="field">
          <label>{{ $t('Codzienny restart') }}</label>
          <div class="row"><input v-model="c.restart_daily_at" type="time" class="input" style="width: 140px" />
            <button v-if="c.restart_daily_at" class="btn sm ghost" @click="c.restart_daily_at = null">{{ $t('Wyłącz') }}</button></div>
          <span class="hint">{{ $t('Gracze dostaną ostrzeżenie 30 s wcześniej. Strefa czasowa kontenera (TZ).') }}</span>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><Network class="hi" /><h3>{{ $t('Proxy') }}</h3></div>
      <div class="card-body col" style="gap: 12px">
        <Toggle v-model="c.proxy_protocol" :label="$t('PROXY protocol v2')" :hint="$t('Przekazuj prawdziwe IP graczy do serwera (wymaga obsługi po stronie serwera)')" />
        <div v-if="c.proxy_protocol" class="callout warn small"><AlertTriangle /><div>{{ $t('Włącz obsługę w serwerze, inaczej połączenia będą odrzucane: Paper/Purpur –') }} <span class="mono">{{ $t('config/paper-global.yml → proxies.proxy-protocol: true') }}</span>{{ $t('; Fabric/Forge – mod typu „HAProxy Detector”/„proxy-protocol-support”. Vanilla tego nie obsługuje.') }}</div></div>
      </div>
    </section>

    <div class="row" style="justify-content: flex-end"><button class="btn primary" :disabled="saving" @click="save"><Save /> {{ $t('Zapisz ustawienia') }}</button></div>

    <VoiceChatCard :server-id="id" />

    <BedrockCard :server-id="id" />

    <section class="card">
      <div class="card-head"><Wrench class="hi" /><h3>{{ $t('Silnik i wersja') }}</h3></div>
      <div class="card-body col" style="gap: 14px">
        <p class="small text-2">{{ $t('Zmień wersję Minecrafta lub silnik. Pliki świata zostaną zachowane, a pliki serwera i Java – pobrane ponownie.') }} <b>{{ $t('Przed aktualizacją zrób kopię zapasową') }}</b> {{ $t('– obniżenie wersji może uszkodzić świat.') }}</p>
        <div class="row wrap" style="gap: 6px">
          <button v-for="(m, k) in ENGINE_META" :key="k" class="chip" :class="{ active: re.type === k }" @click="re.type = k">{{ m.name }}</button>
        </div>
        <VersionPicker :engine="re.type" v-model:mc="re.mc_version" v-model:loader="re.loader_version" />
        <div class="row"><span class="spacer" />
          <button class="btn" :disabled="!re.mc_version || live.summary.state !== 'offline' && live.summary.state !== 'crashed' && live.summary.state !== 'install_failed'" @click="reinstall"><Download /> {{ $t('Zainstaluj {what}', { what: `${ENGINE_META[re.type].name} ${re.mc_version}` }) }}</button>
        </div>
        <div v-if="!['offline', 'crashed', 'install_failed'].includes(live.summary.state)" class="tiny muted" style="text-align: right">{{ $t('Zatrzymaj serwer, aby zmienić wersję.') }}</div>
      </div>
    </section>

    <section class="card danger-zone">
      <div class="card-head"><AlertOctagon class="hi" /><h3>{{ $t('Strefa niebezpieczna') }}</h3></div>
      <div class="card-body row wrap">
        <div class="grow"><b>{{ $t('Usuń serwer') }}</b><div class="small muted">{{ $t('Usuwa pliki serwera i jego kopie na dysku panelu. Nieodwracalne.') }}</div></div>
        <button class="btn" :disabled="exporting || live.summary.state === 'online'" @click="exportServer"><Download /> {{ exporting ? $t('Eksportowanie…') : $t('Eksportuj .minenodepack') }}</button>
        <button class="btn danger" @click="remove"><Trash2 /> {{ $t('Usuń serwer') }}</button>
      </div>
    </section>
  </div>
</template>
<script setup>
import { inject, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Tag, RefreshCcw, Network, Save, Wrench, Download, AlertOctagon, AlertTriangle, Trash2 } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import VersionPicker from '../../components/VersionPicker.vue'
import VoiceChatCard from '../../components/VoiceChatCard.vue'
import BedrockCard from '../../components/BedrockCard.vue'
import { api, download } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { refreshFleet } from '../../servers'
import { ENGINE_META } from '../../utils'

const live = inject('live')
const reload = inject('reload')
const router = useRouter()
const id = live.summary.id
const c = ref(null)
const aliases = ref('')
const settings = ref(null)
const saving = ref(false)
const exporting = ref(false)
const re = reactive({ type: live.summary.type, mc_version: live.summary.mc_version, loader_version: null })

onMounted(async () => {
  try {
    const [d, s] = await Promise.all([api.get(`/servers/${id}`), api.get('/settings')])
    c.value = d.config
    aliases.value = d.config.aliases.join(', ')
    settings.value = s
  } catch (e) { toastError(e) }
})

async function save() {
  saving.value = true
  try {
    const body = {
      name: c.value.name, subdomain: c.value.subdomain, aliases: aliases.value.split(',').map((a) => a.trim()).filter(Boolean),
      auto_start: c.value.auto_start, auto_restart: c.value.auto_restart, start_on_join: c.value.start_on_join,
      proxy_protocol: c.value.proxy_protocol, restart_daily_at: c.value.restart_daily_at || null,
      idle_shutdown_minutes: c.value.idle_shutdown_minutes || 0,
    }
    const r = await api.patch(`/servers/${id}/config`, body)
    c.value = r.config
    toast('Zapisano', 'success')
    reload()
    refreshFleet()
  } catch (e) { toastError(e) } finally { saving.value = false }
}
async function reinstall() {
  const ok = await confirmDialog({
    title: `Zainstalować ${ENGINE_META[re.type].name} ${re.mc_version}?`,
    message: 'Pliki silnika zostaną podmienione. Świat i konfiguracja zostaną zachowane. Upewnij się, że masz kopię zapasową.',
    confirmText: 'Zainstaluj',
  })
  if (!ok) return
  try { await api.post(`/servers/${id}/reinstall`, { ...re }); router.push(`/servers/${id}/console`) } catch (e) { toastError(e) }
}
async function exportServer() {
  exporting.value = true
  try { download(`/servers/${id}/export`); toast('Eksport rozpoczęty', 'success') } catch (e) { toastError(e) } finally { exporting.value = false }
}
async function remove() {
  const ok = await confirmDialog({ title: 'Usunąć serwer?', message: 'Pliki serwera i kopie na dysku panelu zostaną trwale usunięte. Kopie w zewnętrznych miejscach (NAS, S3, WebDAV) zostaną zachowane.', confirmText: 'Usuń na zawsze', danger: true, typeToConfirm: live.summary.name })
  if (!ok) return
  try { await api.del(`/servers/${id}`); toast('Usunięto serwer', 'success'); refreshFleet(); router.push('/') } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.danger-zone { border-color: color-mix(in srgb, var(--danger) 35%, var(--border)); }
.danger-zone .hi { color: var(--danger); }
</style>
