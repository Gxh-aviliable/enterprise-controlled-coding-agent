<template>
  <div class="login-overlay">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-icon">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="#4f46e5"/>
            <path d="M8 12L16 6L24 12V22C24 23.1046 23.1046 24 22 24H10C8.89543 24 8 23.1046 8 22V12Z" stroke="white" stroke-width="1.5" fill="none"/>
            <circle cx="16" cy="16" r="3" fill="white"/>
          </svg>
        </div>
        <h1>Mini Claude Code</h1>
        <p class="subtitle">Enterprise Agent Console</p>
      </div>

      <div class="tabs">
        <button
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >Sign In</button>
        <button
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >Create Account</button>
      </div>

      <form @submit.prevent="handleSubmit" class="login-form">
        <div class="field">
          <label>Username</label>
          <input
            v-model="username"
            type="text"
            placeholder="Enter your username"
            required
            autocomplete="username"
          />
        </div>
        <div v-if="mode === 'register'" class="field">
          <label>Email</label>
          <input
            v-model="email"
            type="email"
            placeholder="you@example.com"
            required
            autocomplete="email"
          />
        </div>
        <div class="field">
          <label>Password</label>
          <input
            v-model="password"
            type="password"
            placeholder="Enter your password"
            required
            autocomplete="current-password"
          />
        </div>

        <p v-if="auth.error" class="error">{{ auth.error }}</p>

        <button type="submit" class="btn-primary" :disabled="auth.loading">
          <span v-if="auth.loading" class="spinner"></span>
          {{ auth.loading ? '' : (mode === 'login' ? 'Sign In' : 'Create Account') }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { auth } from '../stores/auth.js'

const mode = ref('login')
const username = ref('')
const email = ref('')
const password = ref('')

function switchMode(m) {
  mode.value = m
  auth.error = ''
}

async function handleSubmit() {
  try {
    if (mode.value === 'login') {
      await auth.login(username.value, password.value)
    } else {
      await auth.register(username.value, email.value, password.value)
    }
  } catch {}
}
</script>

<style scoped>
.login-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #f8f9fb 0%, #eef2ff 50%, #f8f9fb 100%);
}

.login-card {
  background: var(--bg-primary);
  border-radius: var(--radius-lg);
  padding: 44px 40px;
  width: 420px;
  max-width: 92vw;
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--border);
}

.login-brand {
  text-align: center;
  margin-bottom: 28px;
}

.brand-icon {
  display: inline-flex;
  margin-bottom: 16px;
}

h1 {
  color: var(--text-primary);
  font-size: var(--text-2xl);
  font-weight: 700;
  margin: 0 0 4px;
  letter-spacing: -0.3px;
}

.subtitle {
  color: var(--text-tertiary);
  font-size: var(--text-md);
  margin: 0;
  font-weight: 400;
}

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 24px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  padding: 3px;
}

.tabs button {
  flex: 1;
  padding: 7px 12px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 4px;
  cursor: pointer;
  font-size: var(--text-base);
  font-weight: 500;
  font-family: var(--font-ui);
  transition: all var(--transition);
}

.tabs button.active {
  background: var(--bg-primary);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.field {
  margin-bottom: 16px;
}

.field label {
  display: block;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: 500;
  margin-bottom: 6px;
}

.field input {
  width: 100%;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: var(--text-md);
  font-family: var(--font-ui);
  outline: none;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.field input::placeholder {
  color: var(--text-tertiary);
}

.field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.error {
  color: #ef4444;
  font-size: var(--text-base);
  margin: 0 0 12px;
  padding: 8px 12px;
  background: #fef2f2;
  border-radius: var(--radius-sm);
  border: 1px solid #fecaca;
}

.btn-primary {
  width: 100%;
  padding: 11px 16px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--text-inverse);
  font-size: var(--text-md);
  font-weight: 600;
  font-family: var(--font-ui);
  cursor: pointer;
  margin-top: 4px;
  transition: background var(--transition);
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
}

.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn-primary:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
