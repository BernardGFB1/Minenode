<template>
  <div class="dchips">
    <button v-for="d in options" :key="d.id" type="button" class="chip" :class="{ active: modelValue.includes(d.id) }" :title="$t(TYPE_LABEL[d.type])" @click="toggle(d.id)">
      <component :is="ICONS[d.type]" class="ci" /> {{ d.type === 'local' ? $t('Dysk panelu') : d.name }}
    </button>
    <router-link v-if="options.length < 2" to="/settings" class="tiny add">{{ $t('+ NAS, S3 lub WebDAV') }}</router-link>
  </div>
</template>
<script setup>
import { HardDrive, FolderSync, Cloud, Globe } from 'lucide-vue-next'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, options: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const ICONS = { local: HardDrive, folder: FolderSync, s3: Cloud, webdav: Globe }
const TYPE_LABEL = { local: 'Dysk panelu', folder: 'Folder / NAS', s3: 'S3', webdav: 'WebDAV' }
function toggle(id) {
  const on = props.modelValue.includes(id)
  const next = on ? props.modelValue.filter((x) => x !== id) : [...props.modelValue, id]
  if (next.length) emit('update:modelValue', next) // at least one destination stays selected
}
</script>
<style scoped>
.dchips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.ci { width: 14px; height: 14px; }
.add { color: var(--accent); margin-left: 4px; }
</style>
