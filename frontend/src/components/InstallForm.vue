<template>
  <div class="col" style="gap: 16px">
    <div class="grid grid-2">
      <div class="field">
        <label>Nazwa serwera</label>
        <input :value="modelValue.name" class="input" maxlength="64" @input="set('name', $event.target.value)" />
      </div>
      <div class="field">
        <label>Subdomena</label>
        <div class="input-group">
          <input :value="modelValue.subdomain" class="input mono" :placeholder="slugify(modelValue.name) || 'modpack'" @input="set('subdomain', $event.target.value)" />
          <span class="addon">.{{ settings?.base_domain || 'twoja-domena' }}</span>
        </div>
      </div>
    </div>
    <div class="field">
      <label>Pamięć RAM <span class="muted" style="font-weight: 400">– paczki modów zwykle potrzebują 6–10 GB</span></label>
      <RamPicker :min="modelValue.min_ram_mb" :max="modelValue.max_ram_mb" :total-bytes="sys?.mem_total"
                 @update:min="set('min_ram_mb', $event)" @update:max="set('max_ram_mb', $event)" />
    </div>
    <label class="eula">
      <input type="checkbox" :checked="modelValue.eula" @change="set('eula', $event.target.checked)" />
      <span>Akceptuję <a href="https://aka.ms/MinecraftEULA" target="_blank" rel="noopener">Minecraft EULA</a>.</span>
    </label>
  </div>
</template>
<script setup>
import RamPicker from './RamPicker.vue'
import { slugify } from '../utils'
const props = defineProps({ modelValue: Object, settings: Object, sys: Object })
const emit = defineEmits(['update:modelValue'])
const set = (k, v) => emit('update:modelValue', { ...props.modelValue, [k]: v })
</script>
<style scoped>
.eula { display: flex; gap: 10px; align-items: center; padding: 11px 14px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border); cursor: pointer; font-size: 13px; }
</style>
