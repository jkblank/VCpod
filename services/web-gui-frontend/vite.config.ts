import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-only: proxies /api straight to the FastAPI backend so the
    // frontend can use plain relative fetch('/api/...') calls without
    // hardcoding a backend origin or needing CORS for the common case.
    // The backend's CORS middleware (see web_gui_backend/app.py) is a
    // fallback for hitting it directly, not the primary dev path.
    proxy: {
      '/api': 'http://127.0.0.1:8420',
    },
  },
})
