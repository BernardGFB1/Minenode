<template>
  <div class="shell" :class="{ open: ui.sidebarOpen }">
    <aside class="sidebar">
      <router-link to="/" class="brand" @click="ui.sidebarOpen = false">
        <BrandMark />
        <div>
          <div class="brand-name">MineNode</div>
          <div class="brand-sub">Minecraft server panel</div>
        </div>
      </router-link>

      <nav class="nav">
        <router-link to="/" class="nav-item" exact-active-class="active"><LayoutGrid /> Serwery</router-link>
        <router-link to="/new" class="nav-item" active-class="active"><Plus /> Nowy serwer</router-link>
        <router-link to="/modpacks" class="nav-item" active-class="active"><Package /> Modpacki</router-link>
        <router-link to="/settings" class="nav-item" active-class="active"><Settings2 /> Ustawienia</router-link>
      </nav>

      <div class="nav-section">
        <div class="nav-label">Twoje serwery</div>
        <div class="server-links">
          <router-link v-for="s in fleet.servers" :key="s.id" :to="`/servers/${s.id}`" class="server-link" active-class="active"
                       @click="ui.sidebarOpen = false">
            <span class="dot" :class="STATE[s.state]?.color" />
            <span class="ellipsis grow">{{ s.name }}</span>
            <span v-if="s.state === 'online'" class="tiny muted num">{{ s.players_online }}/{{ s.max_players }}</span>
          </router-link>
          <div v-if="fleet.loaded && !fleet.servers.length" class="tiny muted" style="padding: 4px 10px">Brak serwerów</div>
        </div>
      </div>

      <div class="sidebar-foot">
        <div class="user">
          <div class="avatar">{{ (auth.user || '?')[0].toUpperCase() }}</div>
          <div class="grow ellipsis">
            <div class="small" style="font-weight: 550">{{ auth.user }}</div>
            <div class="tiny muted">v{{ auth.version }}</div>
          </div>
          <button class="btn ghost sm icon" :title="ui.theme === 'dark' ? 'Jasny motyw' : 'Ciemny motyw'" @click="toggleTheme">
            <Sun v-if="ui.theme === 'dark'" /><Moon v-else />
          </button>
          <button class="btn ghost sm icon" title="Wyloguj" @click="logout"><LogOut /></button>
        </div>
      </div>
    </aside>
    <div class="scrim" @click="ui.sidebarOpen = false" />
    <main class="main">
      <button class="btn ghost icon menu-btn" @click="ui.sidebarOpen = true"><Menu /></button>
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { LayoutGrid, Plus, Package, Settings2, LogOut, Sun, Moon, Menu } from 'lucide-vue-next'
import BrandMark from './BrandMark.vue'
import { api } from '../api'
import { auth, ui, toggleTheme } from '../store'
import { fleet, useFleet } from '../servers'
import { STATE } from '../utils'

const router = useRouter()
const release = useFleet()
onUnmounted(release)

async function logout() {
  await api.post('/auth/logout').catch(() => {})
  auth.user = null
  router.push('/login')
}
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.sidebar {
  position: fixed; inset: 0 auto 0 0; width: var(--sidebar-w); display: flex; flex-direction: column;
  background: var(--surface); border-right: 1px solid var(--border); padding: 16px 12px; z-index: 30;
}
.brand { display: flex; align-items: center; gap: 11px; padding: 6px 8px 18px; color: var(--text); }
.brand:hover { text-decoration: none; }
.brand-name { font-weight: 650; font-size: 15.5px; letter-spacing: -0.01em; }
.brand-sub { font-size: 11.5px; color: var(--muted); }
.nav { display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: var(--radius-sm);
  color: var(--text-2); font-weight: 500;
}
.nav-item svg { width: 17px; height: 17px; }
.nav-item:hover { background: var(--surface-3); color: var(--text); text-decoration: none; }
.nav-item.active { background: var(--accent-soft); color: var(--accent); }
.nav-section { margin-top: 22px; min-height: 0; display: flex; flex-direction: column; flex: 1; }
.nav-label { font-size: 12px; color: var(--muted); padding: 0 10px 6px; font-weight: 550; }
.server-links { overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }
.server-link { display: flex; align-items: center; gap: 9px; padding: 6px 10px; border-radius: var(--radius-sm); color: var(--text-2); font-size: 13px; }
.server-link:hover { background: var(--surface-3); color: var(--text); text-decoration: none; }
.server-link.active { background: var(--surface-3); color: var(--text); }
.dot.green { background: var(--accent); }
.dot.amber { background: var(--warn); }
.dot.red { background: var(--danger); }
.dot.blue { background: var(--info); }
.sidebar-foot { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 12px; }
.user { display: flex; align-items: center; gap: 8px; padding: 0 4px; }
.avatar { width: 30px; height: 30px; border-radius: 8px; background: var(--surface-3); display: grid; place-items: center; font-weight: 600; color: var(--accent); }
.main { flex: 1; margin-left: var(--sidebar-w); min-width: 0; }
.menu-btn { display: none; position: fixed; top: 10px; left: 10px; z-index: 20; background: var(--surface); border-color: var(--border); }
.scrim { display: none; }
@media (max-width: 900px) {
  .sidebar { transform: translateX(-100%); transition: transform 0.2s ease; box-shadow: var(--shadow); }
  .shell.open .sidebar { transform: none; }
  .shell.open .scrim { display: block; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 25; }
  .main { margin-left: 0; padding-top: 44px; }
  .menu-btn { display: inline-flex; }
}
</style>
