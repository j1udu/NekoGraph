<script setup lang="ts">
import { Activity, Boxes, Clock3, Database, RefreshCw, Wrench } from '@lucide/vue'
import { NButton, NDataTable, NEmpty, NSpin, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { api } from '../api'
import type { LogEntry } from '../types'
import PageHeader from '../components/layout/PageHeader.vue'
import { useRuntimeStore } from '../stores/runtime'

const runtime = useRuntimeStore()
const { status, loading } = storeToRefs(runtime)
const logs = ref<LogEntry[]>([])
const error = ref('')
const columns: DataTableColumns<LogEntry> = [
  { title: '时间', key: 'timestamp', render: (row) => time(row.timestamp) },
  { title: '级别', key: 'level', render: (row) => row.level },
  { title: '事件', key: 'event' },
  { title: '模块', key: 'logger' },
]

const uptime = computed(() => {
  const seconds = status.value?.uptime_seconds ?? 0
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
})

function time(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [, recent] = await Promise.all([runtime.refresh(), api.logs(8)])
    logs.value = recent.reverse()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <PageHeader title="运行概览" :description="`NekoGraph v${status?.version ?? '—'}`">
      <NButton secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</NButton>
    </PageHeader>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading && !status" class="loading"><NSpin size="small" /> 正在读取运行状态…</div>

    <template v-else-if="status">
      <div class="metrics">
        <div class="metric green">
          <div class="metric-top"><span>Agent Runtime</span><Activity :size="18" /></div>
          <strong>Running</strong>
          <small>LangGraph + SQLite</small>
        </div>
        <div class="metric blue">
          <div class="metric-top"><span>当前模型</span><Boxes :size="18" /></div>
          <strong>{{ status.model.model }}</strong>
          <small>{{ status.model.name }}</small>
        </div>
        <div class="metric amber">
          <div class="metric-top"><span>模型配置</span><Database :size="18" /></div>
          <strong>{{ status.model_profile_count }}</strong>
          <small>已导入 Profile</small>
        </div>
        <div class="metric red">
          <div class="metric-top"><span>运行时间</span><Clock3 :size="18" /></div>
          <strong>{{ uptime }}</strong>
          <small>{{ status.tool_count }} 个已注册工具</small>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h2>最近活动</h2>
          <Wrench :size="17" />
        </div>
        <NEmpty v-if="logs.length === 0" description="暂无运行日志" />
        <NDataTable v-else :columns="columns" :data="logs" :row-key="(row) => `${row.timestamp}-${row.event}`" :bordered="false" />
      </div>
    </template>
  </section>
</template>
