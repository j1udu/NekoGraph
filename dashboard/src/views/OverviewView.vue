<script setup lang="ts">
import { Activity, Boxes, Clock3, Database, RefreshCw, Wrench } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import type { LogEntry, RuntimeStatus } from '../types'

const status = ref<RuntimeStatus | null>(null)
const logs = ref<LogEntry[]>([])
const loading = ref(true)
const error = ref('')

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
    const [runtime, recent] = await Promise.all([api.status(), api.logs(8)])
    status.value = runtime
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
    <header class="page-header">
      <div>
        <h1>运行概览</h1>
        <p>NekoGraph v{{ status?.version ?? '—' }}</p>
      </div>
      <button class="button secondary" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="16" /> 刷新
      </button>
    </header>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading && !status" class="loading">正在读取运行状态…</div>

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
        <div v-if="logs.length === 0" class="empty-state">暂无运行日志</div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead><tr><th>时间</th><th>级别</th><th>事件</th><th>模块</th></tr></thead>
            <tbody>
              <tr v-for="entry in logs" :key="`${entry.timestamp}-${entry.event}`">
                <td>{{ time(entry.timestamp) }}</td>
                <td><span class="badge" :class="entry.level === 'error' ? 'error' : 'inactive'">{{ entry.level }}</span></td>
                <td class="table-title">{{ entry.event }}</td>
                <td class="table-subtitle">{{ entry.logger }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>
