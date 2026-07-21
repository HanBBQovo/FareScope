import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const apiPort = process.env.APP_API_PORT || '16824'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        chunkFileNames: 'assets/chunk-[hash].js',
        manualChunks(id) {
          if (id.includes('/node_modules/recharts/') || id.includes('/node_modules/d3-')) {
            return 'charts-vendor'
          }
          if (id.includes('/node_modules/@radix-ui/')) return 'radix-vendor'
          if (id.includes('/node_modules/lucide-react/')) return 'icons-vendor'
          if (id.includes('/node_modules/@tanstack/react-query/')) return 'query-vendor'
          if (
            id.includes('/node_modules/react/')
            || id.includes('/node_modules/react-dom/')
            || id.includes('/node_modules/react-router/')
            || id.includes('/node_modules/react-router-dom/')
            || id.includes('/node_modules/scheduler/')
          ) {
            return 'react-vendor'
          }
          return undefined
        },
      },
    },
  },
  server: {
    port: 5278,
    proxy: {
      '/api': {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
})
