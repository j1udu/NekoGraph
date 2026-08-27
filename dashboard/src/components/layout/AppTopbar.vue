<script setup lang="ts">
import { Menu } from '@lucide/vue'
import { NButton, NLayoutHeader } from 'naive-ui'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

defineEmits<{ toggleMenu: [] }>()
const route = useRoute()
const titles: Record<string, string> = { '/': '运行概览', '/chat': '本地对话', '/models': '模型配置', '/tools': '工具注册表', '/knowledge': '专题知识库', '/biliwatch': 'B站推送', '/scheduled-tasks': '定时任务', '/logs': '运行日志', '/settings': '运行设置' }
const title = computed(() => titles[route.path] ?? 'NekoGraph')
</script>

<template>
  <NLayoutHeader bordered class="topbar">
    <NButton quaternary circle class="mobile-menu" aria-label="打开导航" @click="$emit('toggleMenu')"><Menu :size="20" /></NButton>
    <strong>{{ title }}</strong>
    <span class="topbar-caption">NekoGraph Agent Console</span>
  </NLayoutHeader>
</template>
