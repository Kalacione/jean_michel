/**
 * plugins/vuetify.ts
 *
 * Framework documentation: https://vuetifyjs.com`
 */

// Composables
import { createVuetify } from 'vuetify'
// VFileUpload is still a labs component in Vuetify 4 — register it explicitly.
import { VFileUpload } from 'vuetify/labs/VFileUpload'
// Styles
import '@mdi/font/css/materialdesignicons.css'

import 'vuetify/styles'

// https://vuetifyjs.com/en/introduction/why-vuetify/#feature-guides
export default createVuetify({
  components: { VFileUpload },
  theme: {
    defaultTheme: 'system',
  },
})
