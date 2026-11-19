<template>
  <select v-if="domains.length > 1" class="addon addon-select mono" :value="current" :title="$t('Domena główna')"
          @change="$emit('update:modelValue', $event.target.value || null)">
    <option v-for="(d, i) in domains" :key="d" :value="i === 0 ? '' : d">.{{ d }}</option>
  </select>
  <span v-else class="addon">.{{ domains[0] || settings?.public_address || $t('twoja-domena') }}{{ suffix }}</span>
</template>
<script setup>
import { computed } from 'vue'

// The ".domain" part next to a subdomain field. With several main domains it becomes a picker;
// the value is the chosen domain, or null for the default (first) one.
const props = defineProps({ settings: Object, modelValue: String, suffix: { type: String, default: '' } })
defineEmits(['update:modelValue'])
const domains = computed(() => props.settings?.domains || (props.settings?.base_domain ? [props.settings.base_domain] : []))
// '' = the default (first) domain; a stored domain that equals the default or is gone shows as the default
const current = computed(() => (props.modelValue && domains.value.indexOf(props.modelValue) > 0 ? props.modelValue : ''))
</script>
<style scoped>
.addon-select { appearance: auto; cursor: pointer; padding-right: 6px; max-width: 55%; font-size: 13px; }
</style>
