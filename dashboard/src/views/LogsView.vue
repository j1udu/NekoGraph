<script setup lang="ts">
import { Pause, Play, RefreshCw } from '@lucide/vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '../api'
import type { LogEntry } from '../types'

const logs = ref<LogEntry[]>([])
const live = ref(true)
const error = ref('')
let timer: number | undefined

function format(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}

async function load() {
  try { logs.value = (await api.logs(200)).reverse(); error.value = '' }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '日志读取失败' }
}

function toggle() { live.value = !live.value }
onMounted(() => { void load(); timer = window.setInterval(() => { if (live.value) void load() }, 2000) })
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div><h1>运行日志</h1><p>{{ live ? 'Live' : 'Paused' }}</p></div>
      <div class="page-actions">
        <button class="button secondary" type="button" @click="toggle"><component :is="live ? Pause : Play" :size="16" /> {{ live ? '暂停' : '继续' }}</button>
        <button class="button secondary" type="button" @click="load"><RefreshCw :size="16" /> 刷新</button>
      </div>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="panel log-panel">
      <div v-if="logs.length === 0" class="empty-state">暂无日志</div>
      <div v-else class="log-list">
        <div v-for="entry in logs" :key="`${entry.timestamp}-${entry.event}`" class="log-row">
          <time>{{ format(entry.timestamp) }}</time>
          <span class="log-level" :class="entry.level">{{ entry.level }}</span>
          <code>{{ entry.event }}</code>
          <span>{{ entry.logger }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.log-panel { overflow: hidden; }
.log-list { max-height: calc(100vh - 170px); overflow: auto; background: #1f2724; color: #dce5e0; padding: 8px 0; }
.log-row { min-height: 35px; padding: 7px 14px; display: grid; grid-template-columns: 128px 66px minmax(200px, 1fr) minmax(130px, .5fr); gap: 10px; align-items: start; border-bottom: 1px solid #303a36; font-size: 11px; }
.log-row time, .log-row > span:last-child { color: #94a49c; }
.log-row code { white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.log-level { font-weight: 800; text-transform: uppercase; }
.log-level.error { color: #ff8e8e; }.log-level.warning { color: #f3bf69; }.log-level.info { color: #78c7ff; }
@media (max-width: 760px) { .log-row { grid-template-columns: 105px 56px minmax(180px, 1fr); } .log-row > span:last-child { display: none; } }
</style>
