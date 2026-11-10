import { createApp } from 'vue'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/pixelify-sans/400.css'
import '@fontsource/pixelify-sans/700.css'
import './styles/main.css'
import App from './App.vue'
import { router } from './router'
import { applyTheme } from './store'

applyTheme()
createApp(App).use(router).mount('#app')
