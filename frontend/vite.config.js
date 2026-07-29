import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // 0.0.0.0 so the dev server is reachable from outside the Docker
    // container, not just from localhost inside it.
    host: true,
    port: 5173,
  },
})
