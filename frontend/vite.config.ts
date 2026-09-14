import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// OneHub 管理台前端。Vite dev 代理 /api → 后端（管理台接口全挂在 /api 前缀）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})