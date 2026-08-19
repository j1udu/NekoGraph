<script setup lang="ts">
import { RefreshCw } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { api } from '../api'
import type { ToolInfo } from '../types'

const tools = ref<ToolInfo[]>([])
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try { tools.value = await api.tools() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '加载失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div><h1>工具注册表</h1><p>{{ tools.length }} registered tools</p></div>
      <button class="button secondary" type="button" @click="load"><RefreshCw :size="16" /> 刷新</button>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="panel">
      <div v-if="loading" class="empty-state">正在读取工具…</div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead><tr><th>工具</th><th>来源</th><th>风险</th><th>权限</th><th>超时</th></tr></thead>
          <tbody>
            <tr v-for="tool in tools" :key="tool.name">
              <td><div class="table-title">{{ tool.name }}</div><div class="table-subtitle">{{ tool.description }}</div></td>
              <td>{{ tool.source }}</td>
              <td><span class="badge" :class="tool.risk">{{ tool.risk }}</span></td>
              <td>{{ tool.required_permissions.join(', ') || '—' }}</td>
              <td>{{ tool.timeout_seconds }}s</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
