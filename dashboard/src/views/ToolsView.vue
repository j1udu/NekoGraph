<script setup lang="ts">
import { RefreshCw } from '@lucide/vue'
import { NButton, NDataTable, NEmpty, NSpin, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { onMounted, ref } from 'vue'

import { api } from '../api'
import type { ToolInfo } from '../types'
import PageHeader from '../components/layout/PageHeader.vue'

const tools = ref<ToolInfo[]>([])
const loading = ref(true)
const error = ref('')
const columns: DataTableColumns<ToolInfo> = [
  { title: '工具', key: 'name', render: (row) => `${row.name}：${row.description}` },
  { title: '来源', key: 'source' },
  { title: '风险', key: 'risk', render: (row) => row.risk },
  { title: '权限', key: 'required_permissions', render: (row) => row.required_permissions.join(', ') || '—' },
  { title: '超时', key: 'timeout_seconds', render: (row) => `${row.timeout_seconds}s` },
]

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
    <PageHeader title="工具注册表" :description="`${tools.length} 个已注册工具`">
      <NButton secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</NButton>
    </PageHeader>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="panel">
      <div v-if="loading" class="empty-state"><NSpin size="small" /> 正在读取工具…</div>
      <NEmpty v-else-if="tools.length === 0" description="暂无已注册工具" />
      <NDataTable v-else :columns="columns" :data="tools" :bordered="false" />
    </div>
  </section>
</template>
