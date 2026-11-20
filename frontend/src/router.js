import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import { auth } from './store'

const routes = [
  { path: '/login', name: 'login', component: () => import('./views/Login.vue'), meta: { public: true } },
  { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/new', name: 'create', component: () => import('./views/CreateServer.vue') },
  { path: '/modpacks', name: 'modpacks', component: () => import('./views/Modpacks.vue') },
  { path: '/auth', name: 'auth', component: () => import('./views/Auth.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue') },
  { path: '/:key/stats', name: 'server-stats', component: () => import('./views/PublicStats.vue'), meta: { public: true } },
  {
    path: '/servers/:id',
    component: () => import('./views/server/ServerView.vue'),
    props: true,
    children: [
      { path: '', name: 'server', component: () => import('./views/server/Overview.vue') },
      { path: 'console', name: 'server-console', component: () => import('./views/server/ConsoleTab.vue') },
      { path: 'players', name: 'server-players', component: () => import('./views/server/PlayersTab.vue') },
      { path: 'public', name: 'server-public', component: () => import('./views/server/PublicPanelTab.vue') },
      { path: 'game', name: 'server-game', component: () => import('./views/server/PropertiesTab.vue') },
      {
        path: 'addons',
        component: () => import('./views/server/AddonsView.vue'),
        children: [
          { path: '', name: 'server-addons', redirect: (to) => `/servers/${to.params.id}/addons/content` },
          { path: 'content', name: 'server-addons-content', component: () => import('./views/server/ContentTab.vue') },
          { path: 'resourcepacks', name: 'server-addons-resourcepacks', component: () => import('./views/server/ResourcePacksTab.vue') },
          { path: 'datapacks', name: 'server-addons-datapacks', component: () => import('./views/server/DatapacksTab.vue') },
          { path: 'services', name: 'server-addons-services', component: () => import('./views/server/ServicesTab.vue') },
        ],
      },
      { path: 'files', name: 'server-files', component: () => import('./views/server/FilesTab.vue') },
      { path: 'backups', name: 'server-backups', component: () => import('./views/server/BackupsTab.vue') },
      {
        path: 'settings',
        component: () => import('./views/server/SettingsView.vue'),
        children: [
          { path: '', name: 'server-settings', component: () => import('./views/server/SettingsTab.vue') },
          { path: 'java', name: 'server-settings-java', component: () => import('./views/server/JavaTab.vue') },
        ],
      },
      // old addresses (bookmarks) of tabs that moved
      { path: 'content', redirect: (to) => `/servers/${to.params.id}/addons/content` },
      { path: 'resourcepacks', redirect: (to) => `/servers/${to.params.id}/addons/resourcepacks` },
      { path: 'java', redirect: (to) => `/servers/${to.params.id}/settings/java` },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  if (!auth.ready) {
    // Retry while the backend is (re)starting – a failed request must not look like "logged out".
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        const s = await api.get('/auth/state')
        auth.user = s.user
        auth.setupRequired = s.setup_required
        auth.version = s.version
        break
      } catch {
        await new Promise((r) => setTimeout(r, Math.min(4000, 400 * 2 ** attempt)))
      }
    }
    auth.ready = true
  }
  if (!to.meta.public && !auth.user) return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
  if (to.name === 'login' && auth.user) return { name: 'dashboard' }
})

window.addEventListener('minenode:unauthorized', () => {
  const current = router.currentRoute.value
  if (current.name !== 'login' && !current.meta.public) router.push({ name: 'login' })
})
