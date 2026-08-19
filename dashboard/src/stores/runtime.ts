import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '../api'
import type { RuntimeStatus } from '../types'

export const useRuntimeStore = defineStore('runtime', () => {
  const status = ref<RuntimeStatus | null>(null)
  const loading = ref(false)
  const error = ref('')
  const lastUpdatedAt = ref<string | null>(null)

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      status.value = await api.status()
      lastUpdatedAt.value = new Date().toISOString()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '运行状态读取失败'
      throw reason
    } finally {
      loading.value = false
    }
  }

  return { status, loading, error, lastUpdatedAt, refresh }
})
