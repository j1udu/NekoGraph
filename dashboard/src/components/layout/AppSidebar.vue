<script setup lang="ts">
import { Boxes, LayoutDashboard, MessageSquareText, Network, ScrollText, Settings, Wrench, X } from '@lucide/vue'
import { NLayoutSider, NMenu } from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import { computed, h } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { useRuntimeStore } from '../../stores/runtime'
import { useUiStore } from '../../stores/ui'
import nekoGraphLogo from '../../assets/nekograph-logo.png'

const ui = useUiStore()
const runtime = useRuntimeStore()
const route = useRoute()
const navigation: Array<{ key: string; label: string; icon: typeof Network }> = [
  { key: '/', label: '概览', icon: LayoutDashboard },
  { key: '/chat', label: '对话', icon: MessageSquareText },
  { key: '/models', label: '模型', icon: Boxes },
  { key: '/tools', label: '工具', icon: Wrench },
  { key: '/logs', label: '日志', icon: ScrollText },
  { key: '/settings', label: '设置', icon: Settings },
]
const options = computed<MenuOption[]>(() => navigation.map((item) => ({
  key: item.key,
  label: () => h(RouterLink, { to: item.key, onClick: ui.closeSidebar }, () => item.label),
  icon: () => h(item.icon, { size: 18 }),
})))
const selectedKey = computed(() => route.path)
</script>

<template>
  <div v-if="ui.sidebarOpen" class="sidebar-scrim" @click="ui.closeSidebar" />
  <NLayoutSider
    bordered
    collapse-mode="transform"
    :collapsed-width="0"
    :width="232"
    :collapsed="false"
    :class="['sidebar', { 'mobile-open': ui.sidebarOpen }]"
  >
    <div class="brand-row">
      <div class="brand-mark"><img :src="nekoGraphLogo" alt="NekoGraph" /></div>
      <div><strong>NekoGraph</strong><span>Agent Console</span></div>
      <button class="close-menu icon-button" type="button" title="关闭导航" @click="ui.closeSidebar"><X :size="19" /></button>
    </div>
    <NMenu :value="selectedKey" :options="options" class="navigation" />
    <div class="sidebar-footer"><span class="status-dot" :class="{ offline: runtime.error }" /><span>{{ runtime.error ? '连接异常' : 'Local only' }}</span></div>
  </NLayoutSider>
</template>
