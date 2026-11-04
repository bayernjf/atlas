import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // P2-3: keep the heavy, rarely-changing vendors in their own chunks so
        // a page code change does not re-download them, and so the entry only
        // pays for React + the login shell. Pages are lazy-loaded in App.tsx.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('@xyflow')) return 'xyflow'
          if (id.includes('/antd/') || id.includes('/@ant-design/') || id.includes('/rc-')) return 'antd'
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
