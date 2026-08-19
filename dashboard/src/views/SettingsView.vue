<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api'
import type { DashboardConfig } from '../types'

const config = ref<DashboardConfig | null>(null)
const error = ref('')
onMounted(async () => {
  try { config.value = await api.config() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '加载失败' }
})
</script>

<template>
  <section class="page">
    <header class="page-header"><div><h1>运行设置</h1><p>Read-only effective configuration</p></div></header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="config" class="settings-stack">
      <section class="panel setting-section">
        <div class="panel-header"><h2>OneBot Gateway</h2></div>
        <dl><div><dt>Listen</dt><dd>{{ config.onebot.host }}:{{ config.onebot.port }}</dd></div><div><dt>Path</dt><dd>{{ config.onebot.path }}</dd></div><div><dt>Access token</dt><dd>{{ config.onebot.access_token_configured ? 'Configured' : 'Not configured' }}</dd></div></dl>
      </section>
      <section class="panel setting-section">
        <div class="panel-header"><h2>Agent State</h2></div>
        <dl><div><dt>Checkpoint</dt><dd>{{ config.agent.checkpoint_backend }}</dd></div><div><dt>Group isolation</dt><dd>{{ config.agent.group_conversation_mode }}</dd></div><div><dt>Wake prefixes</dt><dd>{{ config.agent.group_wake_prefixes.join(', ') }}</dd></div></dl>
      </section>
      <section class="panel setting-section">
        <div class="panel-header"><h2>Tool Policy</h2></div>
        <dl><div><dt>Permissions</dt><dd>{{ config.tools.permissions.join(', ') || 'None' }}</dd></div><div><dt>Dangerous tools</dt><dd>{{ config.tools.allow_dangerous ? 'Enabled' : 'Disabled' }}</dd></div><div><dt>Approval TTL</dt><dd>{{ config.tools.approval_ttl_seconds }}s</dd></div></dl>
      </section>
    </div>
  </section>
</template>

<style scoped>
.settings-stack { display: grid; gap: 16px; }
.setting-section dl { margin: 0; padding: 5px 18px; }
.setting-section dl > div { min-height: 52px; display: grid; grid-template-columns: 180px 1fr; align-items: center; gap: 20px; border-bottom: 1px solid #e8ecea; }
.setting-section dl > div:last-child { border-bottom: 0; }
.setting-section dt { color: #6d7772; font-size: 12px; }
.setting-section dd { margin: 0; overflow-wrap: anywhere; font-size: 13px; }
@media (max-width: 620px) { .setting-section dl > div { grid-template-columns: 1fr; gap: 5px; padding: 10px 0; } }
</style>
