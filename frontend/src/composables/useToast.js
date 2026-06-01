import { ref } from 'vue'

const toasts = ref([])
let nextId = 0

export function useToast() {
  function show(message, type = 'info', duration = 4000) {
    const id = nextId++
    toasts.value.push({ id, message, type })
    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
  }

  function remove(id) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  function error(message, duration = 5000) {
    show(message, 'error', duration)
  }

  function success(message, duration = 3000) {
    show(message, 'success', duration)
  }

  function warning(message, duration = 4000) {
    show(message, 'warning', duration)
  }

  return { toasts, show, error, success, warning, remove }
}
