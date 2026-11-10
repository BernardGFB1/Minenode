import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backend = process.env.MINENODE_BACKEND || 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: { '/api': { target: backend, ws: true, changeOrigin: false } },
  },
})
