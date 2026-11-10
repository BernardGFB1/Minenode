<template>
  <div class="login">
    <LangSwitch class="lang-corner" />
    <div class="panel">
      <div class="row" style="gap: 12px; margin-bottom: 26px">
        <BrandMark />
        <div>
          <div style="font-weight: 650; font-size: 17px">MineNode</div>
          <div class="small muted">{{ $t('Panel zarządzania serwerami Minecraft') }}</div>
        </div>
      </div>
      <h1>{{ setup ? $t('Pierwsze uruchomienie') : $t('Zaloguj się') }}</h1>
      <p class="muted" style="margin: 6px 0 22px">
        {{ setup ? $t('Utwórz konto administratora. Będzie miało pełny dostęp do panelu.') : $t('Podaj dane konta administratora panelu.') }}
      </p>
      <form class="col" style="gap: 14px" @submit.prevent="submit">
        <div class="field">
          <label>{{ $t('Nazwa użytkownika') }}</label>
          <input v-model="username" class="input" autocomplete="username" autofocus required />
        </div>
        <div class="field">
          <label>{{ $t('Hasło') }}</label>
          <input v-model="password" class="input" type="password" :autocomplete="setup ? 'new-password' : 'current-password'" required />
          <span v-if="setup" class="hint">{{ $t('Minimum 8 znaków.') }}</span>
        </div>
        <div v-if="setup" class="field">
          <label>{{ $t('Powtórz hasło') }}</label>
          <input v-model="password2" class="input" type="password" autocomplete="new-password" required />
        </div>
        <div v-if="error" class="callout warn small"><AlertTriangle />{{ $tm(error) }}</div>
        <button class="btn primary" style="height: 40px; margin-top: 4px" :disabled="busy">
          <Loader2 v-if="busy" class="spin" />{{ setup ? $t('Utwórz konto i wejdź') : $t('Zaloguj') }}
        </button>
      </form>
    </div>
    <div class="aside">
      <div class="aside-inner">
        <div class="feature" v-for="f in features" :key="f.t">
          <component :is="f.i" />
          <div><div class="ft">{{ $t(f.t) }}</div><div class="small muted">{{ $t(f.d) }}</div></div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, Loader2, Network, Coffee, Package, Archive } from 'lucide-vue-next'
import BrandMark from '../components/BrandMark.vue'
import LangSwitch from '../components/LangSwitch.vue'
import { api } from '../api'
import { auth } from '../store'
import { t } from '../i18n'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const password2 = ref('')
const error = ref('')
const busy = ref(false)
const setup = computed(() => auth.setupRequired)
const features = [
  { i: Network, t: 'Jeden port, wiele serwerów', d: 'Wbudowany reverse proxy kieruje graczy po subdomenie.' },
  { i: Coffee, t: 'Java dobierana automatycznie', d: 'Właściwe środowisko Temurin dla każdej wersji gry.' },
  { i: Package, t: 'Modpacki jednym kliknięciem', d: 'Import z Modrinth i CurseForge prosto z panelu.' },
  { i: Archive, t: 'Kopie zapasowe', d: 'Spójne backupy z harmonogramem i przywracaniem.' },
]

async function submit() {
  error.value = ''
  if (setup.value && password.value !== password2.value) { error.value = t('Hasła nie są identyczne'); return }
  busy.value = true
  try {
    const r = await api.post(setup.value ? '/auth/setup' : '/auth/login', { username: username.value, password: password.value })
    auth.user = r.user
    auth.setupRequired = false
    router.replace(route.query.next || '/')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>
<style scoped>
.lang-corner { position: fixed; top: 16px; right: 16px; z-index: 5; }
.login { min-height: 100vh; display: grid; grid-template-columns: minmax(380px, 520px) 1fr; }
.panel { padding: 64px 56px; display: flex; flex-direction: column; justify-content: center; background: var(--surface); border-right: 1px solid var(--border); }
.aside { display: grid; place-items: center; padding: 40px; background-color: var(--bg);
  background-image: linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 32px 32px; background-position: -1px -1px;
}
.aside-inner { display: flex; flex-direction: column; gap: 14px; max-width: 400px; }
.feature { display: flex; gap: 14px; padding: 16px 18px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); }
.feature svg { width: 20px; height: 20px; color: var(--accent); flex: none; margin-top: 2px; }
.ft { font-weight: 600; margin-bottom: 2px; }
@media (max-width: 860px) { .login { grid-template-columns: 1fr; } .aside { display: none; } .panel { padding: 40px 24px; } }
</style>
