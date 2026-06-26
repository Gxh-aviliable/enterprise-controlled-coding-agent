import { reactive } from 'vue'
import * as api from '../api/client.js'

export const auth = reactive({
  loggedIn: !!localStorage.getItem('access_token'),
  loading: false,
  error: '',
  notice: '',

  async login(email, password) {
    this.loading = true
    this.error = ''
    this.notice = ''
    try {
      await api.login({ email, password })
      this.loggedIn = true
    } catch (e) {
      this.error = e.message
      throw e
    } finally {
      this.loading = false
    }
  },

  async register(username, email, password) {
    this.loading = true
    this.error = ''
    this.notice = ''
    try {
      await api.register({ username, email, password })
      this.loggedIn = true
    } catch (e) {
      this.error = e.message
      throw e
    } finally {
      this.loading = false
    }
  },

  async forgotPassword(email) {
    this.loading = true
    this.error = ''
    this.notice = ''
    try {
      const data = await api.forgotPassword({ email })
      this.notice = data.message
      return data
    } catch (e) {
      this.error = e.message
      throw e
    } finally {
      this.loading = false
    }
  },

  async resetPassword(email, code, newPassword) {
    this.loading = true
    this.error = ''
    this.notice = ''
    try {
      const data = await api.resetPassword({
        email,
        code,
        new_password: newPassword
      })
      this.notice = data.message
      return data
    } catch (e) {
      this.error = e.message
      throw e
    } finally {
      this.loading = false
    }
  },

  logout() {
    api.clearTokens()
    this.loggedIn = false
  }
})
