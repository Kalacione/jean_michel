/**
 * plugins/index.ts
 *
 * Automatically included in `./src/main.ts`
 */

// Types

// Plugins
import { createPinia } from 'pinia'
import vuetify from './vuetify'

export function registerPlugins (app) {
  app.use(createPinia())
  app.use(vuetify)
}
