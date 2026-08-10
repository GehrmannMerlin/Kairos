import { createApp } from 'vue'

import App from './App.vue'
import { router } from './app/router'
import { authStore } from './features/auth/useAuth'
import './styles/base.css'

async function bootstrap(): Promise<void> {
  // Resolve the initial auth state before mounting so route guards can rely on it.
  await authStore.init()

  const app = createApp(App)
  app.use(router)
  app.mount('#app')
}

void bootstrap()
