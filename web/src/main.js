/**
 * main.ts
 *
 * Bootstraps Vuetify and other plugins then mounts the App`
 */

// Composables
import { createApp } from 'vue'

// Plugins
import { registerPlugins } from '@/plugins'

// Components
import App from './App.vue'

// Styles
import 'katex/dist/katex.min.css'  // KaTeX glyphs/spacing for rendered LaTeX in chat bubbles
import 'unfonts.css'

const app = createApp(App)

registerPlugins(app)

app.mount('#app')
