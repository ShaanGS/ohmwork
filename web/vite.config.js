import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The build lands in web/dist, which ohmwork/server.py mounts if it exists.
// One container, one origin, no CORS: the API and the page it serves are the
// same deployment, which is also why there is no API base URL to configure.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The '@' alias shadcn/ui components import themselves through.
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    // `npm run dev` in front of a local `python -m ohmwork.server`.
    proxy: { '/api': 'http://127.0.0.1:7860' },
  },
})
