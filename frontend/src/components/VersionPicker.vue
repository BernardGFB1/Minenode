<template>
  <div class="vp">
    <div class="row">
      <div class="input-group grow">
        <input v-model="filter" class="input" placeholder="Szukaj wersji, np. 1.21" />
        <span class="addon">{{ filtered.length }} wersji</span>
      </div>
      <Toggle v-if="engine === 'vanilla' || engine === 'fabric' || engine === 'quilt'" v-model="snapshots" label="Snapshoty" />
    </div>
    <div class="versions" :class="{ loading }">
      <div v-if="loading" class="empty small"><Loader2 class="spin" />Pobieranie listy wersji…</div>
      <div v-else-if="error" class="empty small" style="color: var(--danger)">{{ error }}</div>
      <template v-else>
        <button v-for="v in filtered" :key="v.id" type="button" class="vchip" :class="{ active: v.id === mc, pre: !v.stable }" @click="$emit('update:mc', v.id)">
          {{ v.id }}
        </button>
      </template>
    </div>
    <div v-if="engine !== 'vanilla' && mc" class="field">
      <label>{{ loaderLabel }}</label>
      <select class="select" :value="loader || ''" @change="$emit('update:loader', $event.target.value || null)">
        <option value="">Najnowsza stabilna (zalecane)</option>
        <option v-for="l in loaders" :key="l.id" :value="l.id">{{ l.id }}{{ l.recommended ? ' – recommended' : !l.stable ? ' – beta' : '' }}</option>
      </select>
    </div>
  </div>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { Loader2 } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
const props = defineProps({ engine: String, mc: String, loader: String })
const emit = defineEmits(['update:mc', 'update:loader'])
const versions = ref([])
const loaders = ref([])
const filter = ref('')
const snapshots = ref(false)
const loading = ref(false)
const error = ref('')
const loaderLabel = computed(() => ({ paper: 'Build Paper', purpur: 'Build Purpur', fabric: 'Wersja Fabric Loader', quilt: 'Wersja Quilt Loader', forge: 'Wersja Forge', neoforge: 'Wersja NeoForge' })[props.engine])
const filtered = computed(() => versions.value.filter((v) => !filter.value || v.id.includes(filter.value.trim())).slice(0, 160))

async function load() {
  if (!props.engine) return
  loading.value = true
  error.value = ''
  try {
    versions.value = await api.get(`/versions/${props.engine}?snapshots=${snapshots.value}`)
    if (!props.mc || !versions.value.some((v) => v.id === props.mc)) {
      emit('update:mc', (versions.value.find((v) => v.stable) || versions.value[0])?.id || '')
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
async function loadLoaders() {
  loaders.value = []
  if (!props.mc || props.engine === 'vanilla') return
  try { loaders.value = await api.get(`/versions/${props.engine}/${props.mc}/loaders`) } catch {}
}
watch(() => [props.engine, snapshots.value], load, { immediate: true })
watch(() => [props.engine, props.mc], () => { emit('update:loader', null); loadLoaders() }, { immediate: true })
</script>
<style scoped>
.vp { display: flex; flex-direction: column; gap: 12px; }
.versions { display: grid; grid-template-columns: repeat(auto-fill, minmax(86px, 1fr)); gap: 6px; max-height: 230px; overflow-y: auto; padding: 2px; }
.versions .empty { grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; gap: 8px; }
.versions .empty svg { width: 16px; height: 16px; margin: 0; }
.vchip { height: 32px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--surface-2); color: var(--text-2); font: inherit; font-family: var(--mono); font-size: 12.5px; cursor: pointer; }
.vchip:hover { border-color: var(--border-strong); color: var(--text); }
.vchip.pre { color: var(--muted); font-style: italic; }
.vchip.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); font-weight: 600; font-style: normal; }
</style>
