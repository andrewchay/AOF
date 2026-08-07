import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 前端 dev 时把 /v1 代理到后端 FastAPI (8787)
      '/v1': {
        target: 'http://localhost:8787',
        changeOrigin: true,
      },
    },
  },
})
