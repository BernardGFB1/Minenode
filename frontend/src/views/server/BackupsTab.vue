<template>
  <div v-if="data" class="col" style="gap: 16px">
    <div class="grid" style="grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr)">
      <section class="card">
        <div class="card-head"><ArchiveRestore class="hi" /><h3>{{ $t('Nowa kopia zapasowa') }}</h3></div>
        <div class="card-body col" style="gap: 12px">
          <p class="small text-2">{{ $t('Kopia obejmuje cały katalog serwera (świat, konfigurację, mody, pluginy). Gdy serwer działa, zapis świata jest na chwilę wstrzymywany (') }}<span class="mono">{{ $t('save-off') }}</span> / <span class="mono">{{ $t('save-all flush') }}</span>{{ $t('), aby kopia była spójna.') }}</p>
          <form class="row" @submit.prevent="create">
            <input v-model="note" class="input grow" :placeholder="$t('Notatka (opcjonalnie), np. przed aktualizacją modów')" maxlength="200" />
            <button class="btn primary" :disabled="data.busy || creating"><Loader2 v-if="data.busy || creating" class="spin" /><Plus v-else /> {{ $t('Utwórz kopię') }}</button>
          </form>
          <div class="tiny muted">{{ $t('Ostatnia kopia: {when}', { when: data.last_backup_at ? timeAgo(data.last_backup_at) : $t('nigdy') }) }}</div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><CalendarClock class="hi" /><h3>{{ $t('Harmonogram') }}</h3>
          <Toggle v-model="sched.enabled" @update:model-value="saveSchedule" /></div>
        <div class="card-body grid grid-2" :class="{ disabled: !sched.enabled }">
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
          <div class="tiny muted" style="grid-column: 1 / -1">{{ $t('Najstarsze automatyczne kopie są usuwane. Kopie ręczne nie są nigdy kasowane automatycznie.') }}</div>
        </div>
      </section>
    </div>

    <section class="card">
      <div class="card-head"><Archive class="hi" /><h3>{{ $t('Kopie') }}</h3><span class="badge">{{ data.backups.length }}</span>
        <span class="small muted">{{ $t('łącznie') }} {{ formatBytes(data.backups.reduce((a, b) => a + b.size, 0)) }}</span></div>
      <table class="table">
        <thead><tr><th>{{ $t('Data') }}</th><th>{{ $t('Opis') }}</th><th>{{ $t('Wersja') }}</th><th>{{ $t('Rozmiar') }}</th><th /></tr></thead>
        <tbody>
          <tr v-for="b in data.backups" :key="b.id">
            <td><div style="font-weight: 550">{{ formatDate(b.created_at) }}</div><div class="tiny muted">{{ timeAgo(b.created_at) }}</div></td>
            <td><span v-if="b.auto" class="badge blue" style="margin-right: 6px">{{ $t('auto') }}</span><span class="small">{{ $tm(b.note) }}</span></td>
            <td class="small mono muted">{{ b.type }} {{ b.mc_version }}</td>
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
import { Archive, ArchiveRestore, CalendarClock, Plus, Download, RotateCcw, Trash2, Loader2 } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import { api, download } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { formatBytes, formatDate, timeAgo } from '../../utils'

const live = inject('live')
const id = live.summary.id
const data = ref(null)
const note = ref('')
const creating = ref(false)
const sched = reactive({ enabled: false, interval_hours: 24, keep: 7 })

async function load() {
  try {
    data.value = await api.get(`/servers/${id}/backups`)
    Object.assign(sched, data.value.schedule)
  } catch (e) { toastError(e) }
}
onMounted(load)
watch(() => live.job, (j, old) => { if (!j && old) load() })

async function create() {
  creating.value = true
  try { await api.post(`/servers/${id}/backups`, { note: note.value }); note.value = ''; toast('Tworzenie kopii…', 'info', 2000); await load() } catch (e) { toastError(e) } finally { creating.value = false }
}
async function saveSchedule() {
  try { await api.put(`/servers/${id}/backups/schedule`, { ...sched }); toast('Zapisano harmonogram', 'success', 1500) } catch (e) { toastError(e) }
}
async function restore(b) {
  const ok = await confirmDialog({
    title: 'Przywrócić kopię?',
    message: `Serwer zostanie zatrzymany, a obecne pliki zastąpione kopią z ${formatDate(b.created_at)}.\nZalecamy najpierw utworzyć kopię bieżącego stanu.`,
    confirmText: 'Przywróć', danger: true,
  })
  if (!ok) return
  try { await api.post(`/servers/${id}/backups/${b.id}/restore`); toast('Przywrócono kopię', 'success'); load() } catch (e) { toastError(e) }
}
async function remove(b) {
  if (!(await confirmDialog({ title: 'Usunąć kopię?', message: formatDate(b.created_at), confirmText: 'Usuń', danger: true }))) return
  try { await api.del(`/servers/${id}/backups/${b.id}`); load() } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.disabled { opacity: 0.5; pointer-events: none; }
@media (max-width: 900px) { .grid[style] { grid-template-columns: 1fr !important; } }
</style>
