<script setup lang="ts">
import { dateZhCN, NConfigProvider, NDialogProvider, NLayout, NLayoutContent, NMessageProvider, zhCN } from 'naive-ui'
import { onMounted } from 'vue'

import AppSidebar from './components/layout/AppSidebar.vue'
import AppTopbar from './components/layout/AppTopbar.vue'
import GlobalFeedback from './components/GlobalFeedback.vue'
import { themeOverrides } from './plugins/naive-ui'
import { useUiStore } from './stores/ui'
import { useRuntimeStore } from './stores/runtime'

const ui = useUiStore()
const runtime = useRuntimeStore()
onMounted(() => { void runtime.refresh().catch(() => undefined) })
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <NLayout has-sider class="app-shell">
          <AppSidebar />
          <NLayout class="main-layout">
            <AppTopbar @toggle-menu="ui.toggleSidebar" />
            <NLayoutContent class="main-content"><RouterView /></NLayoutContent>
          </NLayout>
        </NLayout>
        <GlobalFeedback />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
