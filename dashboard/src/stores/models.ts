import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '../api'
import type { ModelProfile } from '../types'

export const useModelStore = defineStore('models', () => {
  const profiles = ref<ModelProfile[]>([])
  const loading = ref(false)
  const error = ref('')

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      profiles.value = await api.models()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '模型配置读取失败'
      throw reason
    } finally {
      loading.value = false
    }
  }

  return { profiles, loading, error, refresh }
})
