import { createApp } from 'vue'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/pixelify-sans/400.css'
import '@fontsource/pixelify-sans/700.css'
import './styles/minecraft-font.css'
import './styles/main.css'
import App from './App.vue'
import { router } from './router'
import { applyTheme } from './store'
import i18n from './i18n'

applyTheme()
createApp(App).use(i18n).use(router).mount('#app')
