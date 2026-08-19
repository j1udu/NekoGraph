<script setup lang="ts">
import {
  Boxes,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  Network,
  ScrollText,
  Settings,
  Wrench,
  X,
} from '@lucide/vue'
import { ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

const open = ref(false)
const navigation = [
  { to: '/', label: '概览', icon: LayoutDashboard },
  { to: '/chat', label: '对话', icon: MessageSquareText },
  { to: '/models', label: '模型', icon: Boxes },
  { to: '/tools', label: '工具', icon: Wrench },
  { to: '/logs', label: '日志', icon: ScrollText },
  { to: '/settings', label: '设置', icon: Settings },
]
</script>

<template>
  <div class="app-shell">
    <button class="mobile-menu icon-button" type="button" title="打开导航" @click="open = true">
      <Menu :size="20" />
    </button>
    <div v-if="open" class="sidebar-scrim" @click="open = false" />
    <aside class="sidebar" :class="{ open }">
      <div class="brand-row">
        <div class="brand-mark"><Network :size="21" /></div>
        <div>
          <strong>NekoGraph</strong>
          <span>Agent Console</span>
        </div>
        <button class="close-menu icon-button" type="button" title="关闭导航" @click="open = false">
          <X :size="19" />
        </button>
      </div>

      <nav class="navigation" aria-label="主导航">
        <RouterLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          @click="open = false"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-footer">
        <span class="status-dot" />
        <span>Local only</span>
      </div>
    </aside>

    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>
