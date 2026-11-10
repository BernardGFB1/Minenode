<template>
  <div v-if="data" class="col" style="gap: 16px">
    <div v-for="(err, did) in data.errors" :key="did" class="callout warn small">
      <AlertTriangle /><div>{{ $t('Nie udało się odczytać kopii z „{name}”:', { name: destName(did) }) }} {{ $tm(err) }}</div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr)">
      <section class="card">
        <div class="card-head"><ArchiveRestore class="hi" /><h3>{{ $t('Nowa kopia zapasowa') }}</h3></div>
        <div class="card-body col" style="gap: 12px">
          <p class="small text-2">{{ $t('Kopia obejmuje cały katalog serwera (świat, konfigurację, mody, pluginy). Gdy serwer działa, zapis świata jest na chwilę wstrzymywany (') }}<span class="mono">save-off</span> / <span class="mono">save-all flush</span>{{ $t('), aby kopia była spójna.') }}</p>
          <form class="row" @submit.prevent="create">
            <input v-model="note" class="input grow" :placeholder="$t('Notatka (opcjonalnie), np. przed aktualizacją modów')" maxlength="200" />
            <button class="btn primary" :disabled="data.busy || creating"><Loader2 v-if="data.busy || creating" class="spin" /><Plus v-else /> {{ $t('Utwórz kopię') }}</button>
          </form>
          <div class="field">
            <label>{{ $t('Zapisz w') }}</label>
            <DestChips v-model="target" :options="data.destinations" />
          </div>
          <div class="tiny muted">{{ $t('Ostatnia kopia: {when}', { when: data.last_backup_at ? timeAgo(data.last_backup_at) : $t('nigdy') }) }}</div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><CalendarClock class="hi" /><h3>{{ $t('Harmonogram') }}</h3>
          <Toggle v-model="sched.enabled" @update:model-value="saveSchedule" /></div>
        <div class="card-body col" style="gap: 14px" :class="{ disabled: !sched.enabled }">
          <div class="grid grid-2">
            <div class="field">
              <label>{{ $t('Co ile') }}</label>
              <select v-model.number="sched.interval_hours" class="select" @change="saveSchedule">
                <option v-for="h in [1, 3, 6, 12, 24, 48, 168]" :key="h" :value="h">{{ h === 168 ? $t('tydzień') : h === 1 ? $t('godzinę') : h + ' h' }}</option>
              </select>
            </div>
            <div class="field">
              <label>{{ $t('Zachowaj automatycznych') }}</label>
              <input v-model.number="sched.keep" type="number" min="1" max="500" class="input" @change="saveSchedule" />
            </div>
          </div>
          <div class="field">
            <label>{{ $t('Zapisuj do') }}</label>
            <DestChips :model-value="sched.destinations" :options="data.destinations" @update:model-value="sched.destinations = $event; saveSchedule()" />
          </div>
          <div class="tiny muted">{{ $t('Najstarsze automatyczne kopie są usuwane (osobno w każdym miejscu). Kopie ręczne nie są nigdy kasowane automatycznie.') }}</div>
        </div>
      </section>
    </div>

    <section class="card">
      <div class="card-head"><Archive class="hi" /><h3>{{ $t('Kopie') }}</h3><span class="badge">{{ data.backups.length }}</span>
        <span class="small muted">{{ $t('łącznie') }} {{ formatBytes(data.backups.reduce((a, b) => a + b.size * b.locations.length, 0)) }}</span>
        <button class="btn sm ghost icon" :title="$t('Odśwież')" @click="load"><RefreshCw /></button></div>
      <table class="table">
        <thead><tr><th>{{ $t('Data') }}</th><th>{{ $t('Opis') }}</th><th>{{ $t('Miejsce') }}</th><th>{{ $t('Rozmiar') }}</th><th /></tr></thead>
        <tbody>
          <tr v-for="b in data.backups" :key="b.id">
            <td><div style="font-weight: 550">{{ formatDate(b.created_at) }}</div><div class="tiny muted">{{ timeAgo(b.created_at) }} · <span class="mono">{{ b.type }} {{ b.mc_version }}</span></div></td>
            <td><span v-if="b.auto" class="badge blue" style="margin-right: 6px">{{ $t('auto') }}</span><span class="small">{{ $tm(b.note) }}</span></td>
            <td>
              <div class="row wrap" style="gap: 4px">
                <span v-for="l in b.locations" :key="l.id" class="badge loc" :class="l.type"><component :is="ICONS[l.type]" class="bi" /> {{ l.type === 'local' ? $t('Dysk panelu') : l.name }}</span>
              </div>
            </td>
            <td class="num small">{{ formatBytes(b.size) }}</td>
            <td>
              <div class="row" style="gap: 4px; justify-content: flex-end">
                <button class="btn sm" @click="download(`/servers/${id}/backups/${b.id}/download`)"><Download /> {{ $t('Pobierz') }}</button>
                <button class="btn sm" :disabled="data.busy" @click="restore(b)"><RotateCcw /> {{ $t('Przywróć') }}</button>
                <button class="btn sm ghost icon" :title="$t('Usuń')" @click="remove(b)"><Trash2 /></button>
              </div>
            </td>
          </tr>
          <tr v-if="!data.backups.length"><td colspan="5" class="empty small">{{ $t('Brak kopii zapasowych.') }}</td></tr>
        </tbody>
      </table>
    </section>
  </div>
  <div v-else class="empty"><Loader2 class="spin" /></div>
</template>
<script setup>
import { inject, onMounted, reactive, ref, watch } from 'vue'
import { Archive, ArchiveRestore, CalendarClock, Plus, Download, RotateCcw, Trash2, Loader2, RefreshCw, AlertTriangle, HardDrive, FolderSync, Cloud, Globe } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import DestChips from '../../components/DestChips.vue'
import { api, download } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { formatBytes, formatDate, timeAgo } from '../../utils'
import { t } from '../../i18n'

const ICONS = { local: HardDrive, folder: FolderSync, s3: Cloud, webdav: Globe }
const live = inject('live')
const id = live.summary.id
const data = ref(null)
const note = ref('')
const creating = ref(false)
const target = ref(['local'])
const sched = reactive({ enabled: false, interval_hours: 24, keep: 7, destinations: ['local'] })
const destName = (did) => {
  const d = data.value?.destinations.find((x) => x.id === did)
  return d ? (d.type === 'local' ? t('Dysk panelu') : d.name) : did
}

async function load() {
  try {
    const first = !data.value
    data.value = await api.get(`/servers/${id}/backups`)
    Object.assign(sched, data.value.schedule)
    if (first) target.value = [...data.value.schedule.destinations]
  } catch (e) { toastError(e) }
}
onMounted(load)
watch(() => live.job, (j, old) => { if (!j && old) load() })

async function create() {
  creating.value = true
  try {
    await api.post(`/servers/${id}/backups`, { note: note.value, destinations: target.value })
    note.value = ''
    toast(t('Tworzenie kopii…'), 'info', 2000)
    await load()
  } catch (e) { toastError(e) } finally { creating.value = false }
}
async function saveSchedule() {
  try { await api.put(`/servers/${id}/backups/schedule`, { ...sched }); toast(t('Zapisano harmonogram'), 'success', 1500) } catch (e) { toastError(e) }
}
async function restore(b) {
  const ok = await confirmDialog({
    title: t('Przywrócić kopię?'),
    message: t('Serwer zostanie zatrzymany, a obecne pliki zastąpione kopią z {when}.\nZalecamy najpierw utworzyć kopię bieżącego stanu.', { when: formatDate(b.created_at) }),
    confirmText: t('Przywróć'), danger: true,
  })
  if (!ok) return
  try { await api.post(`/servers/${id}/backups/${b.id}/restore`); toast(t('Przywrócono kopię'), 'success'); load() } catch (e) { toastError(e) }
}
async function remove(b) {
  const where = b.locations.map((l) => (l.type === 'local' ? t('Dysk panelu') : l.name)).join(', ')
  if (!(await confirmDialog({ title: t('Usunąć kopię?'), message: t('{when} – zostanie usunięta z: {where}.', { when: formatDate(b.created_at), where }), confirmText: t('Usuń'), danger: true }))) return
  try { await api.del(`/servers/${id}/backups/${b.id}`); load() } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.bi { width: 12px; height: 12px; }
.disabled { opacity: 0.5; pointer-events: none; }
.loc.local { background: var(--accent-soft); color: var(--accent); }
.loc.folder { background: var(--warn-soft); color: var(--warn); }
.loc.s3 { background: var(--info-soft); color: var(--info); }
.loc.webdav { background: color-mix(in srgb, #b78be6 16%, transparent); color: #b78be6; }
@media (max-width: 900px) { .grid[style] { grid-template-columns: 1fr !important; } }
</style>
