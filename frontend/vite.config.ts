import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // P2-3: keep the two heavy, rarely-changing vendors in their own chunks
        // so a page code change does not re-download them. Pages are lazy-loaded
        // in App.tsx, so the rest of the dependency graph is left to the bundler:
        // a blanket `antd` chunk forced every antd module (Table, Form,
        // ColorPicker, ...) into one file that the entry preloaded even though
        // the login shell only needs ConfigProvider + App. Letting rolldown split
        // antd per usage keeps 1.1 MB of page-only components out of first paint.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('@xyflow')) return 'xyflow'
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) return 'react-vendor'
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
