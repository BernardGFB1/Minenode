<template>
  <div class="page">
    <div class="page-head">
      <div class="grow">
        <h1>{{ $t('Nowy serwer') }}</h1>
        <div class="sub">{{ $t('Panel pobierze pliki serwera i odpowiednią wersję Javy automatycznie.') }}</div>
      </div>
      <router-link to="/modpacks" class="btn"><Package /> {{ $t('Zamiast tego: modpack') }}</router-link>
    </div>

    <div class="layout">
      <div class="col" style="gap: 16px">
        <section class="card">
          <div class="card-head"><span class="step">1</span><h3>{{ $t('Silnik serwera') }}</h3></div>
          <div class="card-body engines">
            <button v-for="(e, key) in engines" :key="key" type="button" class="engine" :class="{ active: form.type === key }" @click="form.type = key">
              <div class="etile" :style="{ background: ENGINE_META[key].color, color: ENGINE_META[key].ink || '#10140f' }">{{ ENGINE_META[key].letter }}</div>
              <div class="grow" style="min-width: 0">
                <div class="row" style="gap: 6px"><b>{{ e.name }}</b>
                  <span v-if="e.plugins" class="badge blue">{{ $t('Pluginy') }}</span>
                  <span v-if="e.mods" class="badge amber">{{ $t('Mody') }}</span>
                </div>
                <div class="small muted">{{ $t(e.desc) }}</div>
              </div>
              <CheckCircle2 v-if="form.type === key" class="check" />
            </button>
          </div>
        </section>

        <section class="card">
          <div class="card-head"><span class="step">2</span><h3>{{ $t('Wersja Minecrafta') }}</h3></div>
          <div class="card-body">
            <VersionPicker :engine="form.type" v-model:mc="form.mc_version" v-model:loader="form.loader_version" />
          </div>
        </section>

        <section class="card">
          <div class="card-head"><span class="step">3</span><h3>{{ $t('Szczegóły') }}</h3></div>
          <div class="card-body col" style="gap: 18px">
            <div class="grid grid-2">
              <div class="field">
                <label>{{ $t('Nazwa serwera') }}</label>
                <input v-model="form.name" class="input" :placeholder="$t('np. Survival z ekipą')" maxlength="64" />
              </div>
              <div class="field">
                <label>{{ $t('Subdomena') }}</label>
                <div class="input-group">
                  <input v-model="form.subdomain" class="input mono" :placeholder="autoSub || 'survival'" @input="subTouched = true" />
                  <span class="addon">.{{ settings?.base_domain || $t('twoja-domena') }}</span>
                </div>
                <span class="hint">{{ $t('Gracze połączą się pod adresem') }} <b class="mono">{{ address }}</b></span>
              </div>
            </div>
            <div class="field">
              <label>{{ $t('Pamięć RAM') }}</label>
              <RamPicker v-model:min="form.java.min_ram_mb" v-model:max="form.java.max_ram_mb" :total-bytes="sys?.mem_total" :other-bytes="otherBytes" />
            </div>
            <label class="eula">
              <input v-model="form.eula" type="checkbox" />
              <span>{{ $t('Akceptuję') }} <a href="https://aka.ms/MinecraftEULA" target="_blank" rel="noopener">Minecraft EULA</a>{{ $t('. Jest to wymagane przez Mojang do uruchomienia serwera.') }}</span>
            </label>
          </div>
        </section>
      </div>

      <aside class="summary card">
        <div class="card-body col" style="gap: 14px">
          <h3>{{ $t('Podsumowanie') }}</h3>
          <dl>
            <dt>{{ $t('Silnik') }}</dt><dd>{{ engines[form.type]?.name }}</dd>
            <dt>{{ $t('Wersja') }}</dt><dd class="mono">{{ form.mc_version || '—' }}</dd>
            <dt v-if="form.type !== 'vanilla'">{{ $t('Loader/build') }}</dt><dd v-if="form.type !== 'vanilla'" class="mono">{{ form.loader_version || $t('najnowszy') }}</dd>
            <dt>RAM</dt><dd>{{ formatMB(form.java.max_ram_mb) }}</dd>
            <dt>{{ $t('Adres') }}</dt><dd class="mono ellipsis">{{ address }}</dd>
          </dl>
          <div class="callout info small"><Coffee />{{ $t('Java zostanie dobrana automatycznie do wersji {v}.', { v: form.mc_version || $t('gry') }) }}</div>
          <button class="btn primary" style="height: 40px" :disabled="!valid || busy" @click="create">
            <Loader2 v-if="busy" class="spin" /><Rocket v-else /> {{ $t('Utwórz i zainstaluj') }}
          </button>
          <div v-if="!form.eula" class="tiny muted" style="text-align: center">{{ $t('Zaakceptuj EULA, aby kontynuować.') }}</div>
        </div>
      </aside>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Package, CheckCircle2, Coffee, Loader2, Rocket } from 'lucide-vue-next'
import VersionPicker from '../components/VersionPicker.vue'
import RamPicker from '../components/RamPicker.vue'
import { api } from '../api'
import { toast, toastError } from '../store'
import { t } from '../i18n'
import { refreshFleet, fleet } from '../servers'
import { ENGINE_META, formatMB, slugify } from '../utils'

const router = useRouter()
const engines = ref({})
const settings = ref(null)
const sys = ref(null)
const busy = ref(false)
const subTouched = ref(false)
const form = reactive({
  type: 'paper', mc_version: '', loader_version: null, name: '', subdomain: '', eula: false,
  java: { min_ram_mb: 2048, max_ram_mb: 4096, flags_preset: 'aikar', extra_jvm_args: '', extra_server_args: '' },
})
const autoSub = computed(() => slugify(form.name))
const address = computed(() => {
  const sub = form.subdomain || autoSub.value || 'serwer'
  const port = settings.value && settings.value.proxy_port !== 25565 ? `:${settings.value.proxy_port}` : ''
  return `${sub}.${settings.value?.base_domain || settings.value?.public_address || t('twoja-domena')}${port}`
})
const otherBytes = computed(() => fleet.servers.reduce((a, s) => a + s.max_ram_mb * 1048576, 0))
const valid = computed(() => form.name.trim() && form.mc_version && form.eula)

onMounted(async () => {
  try {
    ;[engines.value, settings.value, sys.value] = await Promise.all([api.get('/engines'), api.get('/settings'), api.get('/system')])
  } catch (e) { toastError(e) }
})

async function create() {
  busy.value = true
  try {
    const s = await api.post('/servers', { ...form, subdomain: form.subdomain || null })
    toast(t('Instalowanie serwera „{name}”…', { name: s.name }), 'success')
    refreshFleet()
    router.push(`/servers/${s.id}/console`)
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>
<style scoped>
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 16px; align-items: start; }
.summary { position: sticky; top: 20px; }
@media (max-width: 1050px) { .layout { grid-template-columns: 1fr; } .summary { position: static; } }
.step { width: 22px; height: 22px; border-radius: 6px; background: var(--accent-soft); color: var(--accent); display: grid; place-items: center; font-size: 12px; font-weight: 700; }
.engines { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.engine { display: flex; gap: 12px; align-items: flex-start; text-align: left; padding: 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface-2); color: var(--text); font: inherit; cursor: pointer; position: relative; }
.engine:hover { border-color: var(--border-strong); }
.engine.active { border-color: var(--accent); background: var(--accent-soft); }
.etile { width: 36px; height: 36px; border-radius: 8px; display: grid; place-items: center; font-weight: 700; font-size: 13px; flex: none; }
.check { width: 18px; height: 18px; color: var(--accent); position: absolute; top: 10px; right: 10px; }
.eula { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border); cursor: pointer; font-size: 13px; }
.eula input { margin-top: 2px; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 8px 14px; margin: 0; font-size: 13px; }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; min-width: 0; }
</style>
