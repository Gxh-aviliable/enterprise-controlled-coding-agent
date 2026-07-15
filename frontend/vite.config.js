import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // SSE 长连接需要更长超时（DeepSeek thinking 模型响应慢）
        timeout: 180000, // 180 秒
        proxyTimeout: 180000, // 代理超时
        // 禁用代理重试，避免 ECONNRESET 错误堆积
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // 静默处理 ECONNRESET（客户端主动断开是正常行为）
            if (err.code === 'ECONNRESET') {
              return
            }
            console.log('proxy error:', err)
          })
        }
      }
    }
  }
})
