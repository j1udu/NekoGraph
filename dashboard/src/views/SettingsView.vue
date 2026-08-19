<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NEmpty, NSpin, NTag } from 'naive-ui'

import { api } from '../api'
import type { DashboardConfig } from '../types'
import PageHeader from '../components/layout/PageHeader.vue'

const config = ref<DashboardConfig | null>(null)
const error = ref('')
onMounted(async () => {
  try { config.value = await api.config() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '加载失败' }
})
</script>

<template>
  <section class="page">
    <PageHeader title="运行设置" description="当前程序正在使用的配置（仅查看）" />
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="!config && !error" class="loading"><NSpin size="small" /> 正在读取设置…</div>
    <NEmpty v-else-if="!config" description="暂无设置数据" />
    <div v-else class="settings-stack">
      <section class="panel setting-section">
        <div class="panel-header"><h2>消息接入</h2><span class="section-hint">OneBot 反向 WebSocket</span></div>
        <dl><div><dt>监听地址</dt><dd>{{ config.onebot.host }}:{{ config.onebot.port }}<small>NapCat 连接到这个地址</small></dd></div><div><dt>WebSocket 路径</dt><dd>{{ config.onebot.path }}</dd></div><div><dt>访问令牌</dt><dd>{{ config.onebot.access_token_configured ? '已配置' : '未配置' }}<small>用于验证 OneBot 连接身份</small></dd></div></dl>
      </section>
      <section class="panel setting-section">
        <div class="panel-header"><h2>智能体对话</h2><span class="section-hint">上下文与群聊策略</span></div>
        <dl><div><dt>上下文保存方式</dt><dd>{{ config.agent.checkpoint_backend === 'sqlite' ? 'SQLite 本地数据库' : config.agent.checkpoint_backend }}<small>用于保存多轮对话进度</small></dd></div><div><dt>群聊上下文隔离</dt><dd>{{ config.agent.group_conversation_mode === 'per_user' ? '按群内用户分别保存' : '整个群共享一份上下文' }}<small>决定群聊中不同用户是否共享记忆</small></dd></div><div><dt>群聊唤醒前缀</dt><dd>{{ config.agent.group_wake_prefixes.join('、') }}<small>群聊消息以这些前缀开头时才会触发 Agent</small></dd></div></dl>
      </section>
      <section class="panel setting-section">
        <div class="panel-header"><h2>工具安全</h2><span class="section-hint">工具调用权限与审批</span></div>
        <dl><div><dt>已授权权限</dt><dd>{{ config.tools.permissions.join('、') || '无' }}<small>允许工具执行的权限范围</small></dd></div><div><dt>危险工具</dt><dd>{{ config.tools.allow_dangerous ? '允许执行' : '禁止执行' }}<small>涉及文件、系统或外部副作用的工具</small></dd></div><div><dt>审批有效期</dt><dd>{{ config.tools.approval_ttl_seconds }} 秒<small>用户确认危险操作后，审批保持有效的时间</small></dd></div></dl>
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
.setting-section dd small { display: block; margin-top: 4px; color: #89948f; font-size: 11px; }
.section-hint { color: #89948f; font-size: 11px; }
@media (max-width: 620px) { .setting-section dl > div { grid-template-columns: 1fr; gap: 5px; padding: 10px 0; } }
</style>
