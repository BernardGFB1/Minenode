import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import { auth } from './store'

const routes = [
  { path: '/login', name: 'login', component: () => import('./views/Login.vue'), meta: { public: true } },
  { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/new', name: 'create', component: () => import('./views/CreateServer.vue') },
  { path: '/modpacks', name: 'modpacks', component: () => import('./views/Modpacks.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue') },
  {
    path: '/servers/:id',
    component: () => import('./views/server/ServerView.vue'),
    props: true,
    children: [
      { path: '', name: 'server', component: () => import('./views/server/Overview.vue') },
      { path: 'console', name: 'server-console', component: () => import('./views/server/ConsoleTab.vue') },
      { path: 'players', name: 'server-players', component: () => import('./views/server/PlayersTab.vue') },
      { path: 'game', name: 'server-game', component: () => import('./views/server/PropertiesTab.vue') },
      { path: 'content', name: 'server-content', component: () => import('./views/server/ContentTab.vue') },
      { path: 'resourcepacks', name: 'server-resourcepacks', component: () => import('./views/server/ResourcePacksTab.vue') },
      { path: 'java', name: 'server-java', component: () => import('./views/server/JavaTab.vue') },
      { path: 'files', name: 'server-files', component: () => import('./views/server/FilesTab.vue') },
      { path: 'backups', name: 'server-backups', component: () => import('./views/server/BackupsTab.vue') },
      { path: 'settings', name: 'server-settings', component: () => import('./views/server/SettingsTab.vue') },
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
  if (router.currentRoute.value.name !== 'login') router.push({ name: 'login' })
})
