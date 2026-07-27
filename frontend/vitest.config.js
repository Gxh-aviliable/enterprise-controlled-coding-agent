import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_BUILD_ID__: JSON.stringify('test-build')
  },
  test: {
    environment: 'jsdom',
    restoreMocks: true
  }
})
