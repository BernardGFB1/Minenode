<template>
  <label class="toggle" :class="{ disabled }">
    <input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit('update:modelValue', $event.target.checked)" />
    <span class="track"><span class="thumb" /></span>
    <span v-if="label || $slots.default" class="text">
      <span class="t">{{ label }}<slot /></span>
      <span v-if="hint" class="hint">{{ hint }}</span>
    </span>
  </label>
</template>
<script setup>
defineProps({ modelValue: Boolean, label: String, hint: String, disabled: Boolean })
defineEmits(['update:modelValue'])
</script>
<style scoped>
.toggle { display: inline-flex; align-items: flex-start; gap: 11px; cursor: pointer; user-select: none; }
.toggle.disabled { opacity: 0.55; cursor: not-allowed; }
input { position: absolute; opacity: 0; pointer-events: none; }
.track { flex: none; width: 34px; height: 20px; border-radius: 20px; background: var(--surface-3); border: 1px solid var(--border-strong); position: relative; transition: background 0.15s, border-color 0.15s; margin-top: 1px; }
.thumb { position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: var(--text-2); transition: transform 0.15s, background 0.15s; }
input:checked + .track { background: var(--accent); border-color: var(--accent); }
input:checked + .track .thumb { transform: translateX(14px); background: var(--accent-ink); }
input:focus-visible + .track { box-shadow: var(--ring); }
.text { display: flex; flex-direction: column; gap: 1px; }
.t { font-weight: 500; }
.hint { font-size: 12px; color: var(--muted); }
</style>
