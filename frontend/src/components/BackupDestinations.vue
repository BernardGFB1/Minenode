<template>
  <section class="card">
    <div class="card-head">
      <DatabaseBackup class="hi" /><h3>{{ $t('Miejsca kopii zapasowych') }}</h3>
      <button class="btn sm primary" @click="openNew"><Plus /> {{ $t('Dodaj miejsce') }}</button>
    </div>
    <div class="list">
      <div v-for="d in items" :key="d.id" class="dest">
        <div class="dicon" :class="d.type"><component :is="TYPES[d.type].icon" /></div>
        <div class="grow" style="min-width: 0">
          <div class="row" style="gap: 8px">
            <b class="ellipsis">{{ d.type === 'local' ? $t('Dysk panelu') : d.name }}</b>
            <span v-if="d.type !== 'local'" class="badge">{{ $t(TYPES[d.type].label) }}</span>
            <span v-if="results[d.id]?.ok" class="badge green"><Check class="bi" /> {{ $t('działa') }} · {{ results[d.id].ms }} ms</span>
            <span v-else-if="results[d.id]" class="badge red" :title="$tm(results[d.id].error)"><X class="bi" /> {{ $t('błąd') }}</span>
          </div>
          <div class="tiny muted mono ellipsis">{{ d.summary }}</div>
          <div v-if="results[d.id] && !results[d.id].ok" class="tiny err">{{ $tm(results[d.id].error) }}</div>
          <div v-if="d.used_by.length" class="tiny muted">{{ $t('Używane przez: {list}', { list: d.used_by.join(', ') }) }}</div>
        </div>
        <button class="btn sm" :disabled="testing[d.id]" @click="testSaved(d)"><Loader2 v-if="testing[d.id]" class="spin" /><Activity v-else /> {{ $t('Testuj') }}</button>
        <template v-if="d.type !== 'local'">
          <button class="btn sm ghost icon" :title="$t('Edytuj')" @click="openEdit(d)"><PenLine /></button>
          <button class="btn sm ghost icon" :title="$t('Usuń')" @click="remove(d)"><Trash2 /></button>
        </template>
      </div>
    </div>
    <div class="card-body tiny muted" style="border-top: 1px solid var(--border)">
      {{ $t('Wybierz w zakładce „Kopie zapasowe” serwera, dokąd mają trafiać jego kopie – możesz zapisywać w kilku miejscach jednocześnie.') }}
    </div>

    <Modal :open="!!form" :title="form?.id ? $t('Edytuj miejsce kopii') : $t('Nowe miejsce kopii')" width="620px" @close="form = null">
      <div v-if="form" class="col" style="gap: 16px">
        <div v-if="!form.id" class="types">
          <button v-for="key in ['folder', 's3', 'webdav']" :key="key" type="button" class="type" :class="{ active: form.type === key }" @click="pick(key)">
            <div class="dicon" :class="key"><component :is="TYPES[key].icon" /></div>
            <div><b>{{ $t(TYPES[key].label) }}</b><div class="tiny muted">{{ $t(TYPES[key].desc) }}</div></div>
          </button>
        </div>
        <div class="field">
          <label>{{ $t('Nazwa') }}</label>
          <input v-model="form.name" class="input" maxlength="64" :placeholder="$t(TYPES[form.type].namePh)" />
        </div>

        <template v-if="form.type === 'folder'">
          <div class="field">
            <label>{{ $t('Ścieżka w kontenerze') }}</label>
            <input v-model="form.path" class="input mono" placeholder="/backups/nas" />
          </div>
          <div class="callout info small">
            <Info />
            <div>
              {{ $t('Najpierw zamontuj folder (np. udział NAS) w docker-compose, a potem wpisz tutaj ścieżkę z prawej strony:') }}
              <pre class="snippet">volumes:
  - /mnt/nas/minecraft:/backups/nas</pre>
            </div>
          </div>
        </template>

        <template v-else-if="form.type === 's3'">
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Bucket') }}</label>
              <input v-model="form.bucket" class="input mono" placeholder="minecraft-backups" />
            </div>
            <div class="field">
              <label>{{ $t('Folder w buckecie (prefiks)') }}</label>
              <input v-model="form.prefix" class="input mono" placeholder="minenode" />
            </div>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Access key') }}</label>
              <input v-model="form.access_key" class="input mono" autocomplete="off" />
            </div>
            <div class="field">
              <label>{{ $t('Secret key') }}</label>
              <input v-model="form.secret_key" class="input mono" type="password" autocomplete="new-password" :placeholder="form.secret_key_set ? $t('(bez zmian)') : ''" />
            </div>
          </div>
          <div class="field">
            <label>{{ $t('Dostawca') }}</label>
            <div class="row wrap" style="gap: 6px">
              <button v-for="p in PROVIDERS" :key="p.name" type="button" class="chip" :class="{ active: provider === p.name }" @click="usePreset(p)">{{ p.name }}</button>
            </div>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Endpoint') }} <span class="muted" style="font-weight: 400">{{ $t('(puste = AWS)') }}</span></label>
              <input v-model="form.endpoint" class="input mono" placeholder="https://s3.eu-central-003.backblazeb2.com" />
            </div>
            <div class="field">
              <label>{{ $t('Region') }}</label>
              <input v-model="form.region" class="input mono" placeholder="eu-central-1" />
            </div>
          </div>
          <Toggle v-model="form.path_style" :label="$t('Adresowanie path-style')" :hint="$t('Wymagane przez MinIO i część serwerów S3 hostowanych samodzielnie')" />
        </template>

        <template v-else-if="form.type === 'webdav'">
          <div class="field">
            <label>{{ $t('Adres folderu WebDAV') }}</label>
            <input v-model="form.url" class="input mono" placeholder="https://cloud.example.com/remote.php/dav/files/user/Backups" />
            <span class="hint">{{ $t('Nextcloud/ownCloud: Pliki → Ustawienia → WebDAV. Synology: włącz pakiet WebDAV Server (port 5006 dla HTTPS).') }}</span>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Użytkownik') }}</label>
              <input v-model="form.username" class="input" autocomplete="off" />
            </div>
            <div class="field">
              <label>{{ $t('Hasło') }}</label>
              <input v-model="form.password" class="input" type="password" autocomplete="new-password" :placeholder="form.password_set ? $t('(bez zmian)') : ''" />
              <span class="hint">{{ $t('W Nextcloud użyj hasła aplikacji (Ustawienia → Bezpieczeństwo).') }}</span>
            </div>
          </div>
        </template>

        <div v-if="formTest" class="callout small" :class="formTest.ok ? 'green' : 'warn'">
          <CheckCircle2 v-if="formTest.ok" /><AlertTriangle v-else />
          <div>{{ formTest.ok ? $t('Połączenie działa – zapis, odczyt i usuwanie pliku testowego się powiodły ({ms} ms).', { ms: formTest.ms }) : $tm(formTest.error) }}</div>
        </div>
      </div>
      <template #footer>
        <button class="btn" :disabled="busy" @click="testForm"><Loader2 v-if="busy === 'test'" class="spin" /><Activity v-else /> {{ $t('Testuj połączenie') }}</button>
        <span class="spacer" />
        <button class="btn" @click="form = null">{{ $t('Anuluj') }}</button>
        <button class="btn primary" :disabled="!!busy || !form?.name.trim()" @click="save"><Loader2 v-if="busy === 'save'" class="spin" /><Save v-else /> {{ $t('Zapisz') }}</button>
      </template>
    </Modal>
  </section>
</template>
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { DatabaseBackup, Plus, HardDrive, FolderSync, Cloud, Globe, Check, X, Loader2, Activity, PenLine, Trash2, Info, Save, CheckCircle2, AlertTriangle } from 'lucide-vue-next'
import Modal from './Modal.vue'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { confirmDialog, toast, toastError } from '../store'
import { t } from '../i18n'

const TYPES = {
  local: { icon: HardDrive, label: 'Dysk panelu', desc: '', namePh: '' },
  folder: { icon: FolderSync, label: 'Folder / NAS', desc: 'Zamontowany udział NAS, drugi dysk, folder sieciowy', namePh: 'np. NAS w piwnicy' },
  s3: { icon: Cloud, label: 'S3', desc: 'AWS, Backblaze B2, Cloudflare R2, Wasabi, MinIO', namePh: 'np. Backblaze B2' },
  webdav: { icon: Globe, label: 'WebDAV', desc: 'Nextcloud, ownCloud, Synology, Hetzner Storage Box', namePh: 'np. Nextcloud' },
}
const PROVIDERS = [
  { name: 'AWS', endpoint: '', region: 'eu-central-1', path_style: false },
  { name: 'Backblaze B2', endpoint: 'https://s3.eu-central-003.backblazeb2.com', region: 'eu-central-003', path_style: false },
  { name: 'Cloudflare R2', endpoint: 'https://<ACCOUNT_ID>.r2.cloudflarestorage.com', region: 'auto', path_style: false },
  { name: 'Wasabi', endpoint: 'https://s3.eu-central-1.wasabisys.com', region: 'eu-central-1', path_style: false },
  { name: 'MinIO', endpoint: 'http://minio:9000', region: 'us-east-1', path_style: true },
]
const BLANK = { name: '', type: 'folder', path: '', endpoint: '', region: '', bucket: '', prefix: 'minenode', access_key: '', secret_key: '', path_style: false, url: '', username: '', password: '' }

const items = ref([])
const results = reactive({})
const testing = reactive({})
const form = ref(null)
const formTest = ref(null)
const busy = ref(null)
const provider = ref(null)

async function load() { try { items.value = await api.get('/backup-destinations') } catch (e) { toastError(e) } }
onMounted(load)

function openNew() { form.value = { ...BLANK }; formTest.value = null; provider.value = null }
function openEdit(d) { form.value = { ...BLANK, ...d }; formTest.value = null; provider.value = null }
function pick(type) { form.value.type = type; formTest.value = null }
function usePreset(p) {
  provider.value = p.name
  Object.assign(form.value, { endpoint: p.endpoint, region: p.region, path_style: p.path_style })
  if (!form.value.name) form.value.name = p.name
}
const body = () => { const { id, summary, used_by, secret_key_set, password_set, ...rest } = form.value; return rest }

async function testForm() {
  busy.value = 'test'
  try { formTest.value = await api.post(`/backup-destinations/test${form.value.id ? `?id=${form.value.id}` : ''}`, body()) } catch (e) { formTest.value = { ok: false, error: e.message } } finally { busy.value = null }
}
async function save() {
  busy.value = 'save'
  try {
    if (form.value.id) await api.put(`/backup-destinations/${form.value.id}`, body())
    else await api.post('/backup-destinations', body())
    toast(t('Zapisano miejsce kopii'), 'success')
    form.value = null
    load()
  } catch (e) { formTest.value = { ok: false, error: e.message } } finally { busy.value = null }
}
async function testSaved(d) {
  testing[d.id] = true
  try { results[d.id] = await api.post(`/backup-destinations/${d.id}/test`) } catch (e) { results[d.id] = { ok: false, error: e.message } } finally { testing[d.id] = false }
}
async function remove(d) {
  const ok = await confirmDialog({
    title: t('Usunąć miejsce „{name}”?', { name: d.name }),
    message: t('Panel przestanie zapisywać tam kopie. Pliki, które już tam są, zostaną nietknięte.'),
    confirmText: t('Usuń'), danger: true,
  })
  if (!ok) return
  try { await api.del(`/backup-destinations/${d.id}`); load() } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.bi { width: 12px; height: 12px; }
.list { display: flex; flex-direction: column; padding: 6px 8px; }
.dest { display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: var(--radius-sm); }
.dest:hover { background: var(--surface-2); }
.dicon { width: 36px; height: 36px; border-radius: 9px; display: grid; place-items: center; flex: none; background: var(--surface-3); color: var(--text-2); }
.dicon svg { width: 18px; height: 18px; }
.dicon.local { background: var(--accent-soft); color: var(--accent); }
.dicon.folder { background: var(--warn-soft); color: var(--warn); }
.dicon.s3 { background: var(--info-soft); color: var(--info); }
.dicon.webdav { background: color-mix(in srgb, #b78be6 16%, transparent); color: #b78be6; }
.err { color: var(--danger); }
.types { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 640px) { .types { grid-template-columns: 1fr; } }
.type { display: flex; gap: 10px; align-items: flex-start; text-align: left; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); color: var(--text); font: inherit; cursor: pointer; }
.type.active { border-color: var(--accent); background: var(--accent-soft); }
.snippet { margin: 8px 0 0; padding: 8px 10px; background: var(--console-bg); color: #cfd6d0; border-radius: 6px; font-family: var(--mono); font-size: 12px; }
</style>
