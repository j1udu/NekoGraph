import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createPinia } from 'pinia'

import App from './App.vue'
import ChatView from './views/ChatView.vue'
import LogsView from './views/LogsView.vue'
import ModelsView from './views/ModelsView.vue'
import OverviewView from './views/OverviewView.vue'
import SettingsView from './views/SettingsView.vue'
import ToolsView from './views/ToolsView.vue'
import ScheduledTasksView from './views/ScheduledTasksView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import BiliWatchView from './views/BiliWatchView.vue'
import './styles.css'
import './styles/tokens.css'
import './styles/layout.css'
import './styles/components.css'
import './styles/pages.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewView },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/tools', name: 'tools', component: ToolsView },
    { path: '/scheduled-tasks', name: 'scheduled-tasks', component: ScheduledTasksView },
    { path: '/knowledge', name: 'knowledge', component: KnowledgeView },
    { path: '/biliwatch', name: 'biliwatch', component: BiliWatchView },
    { path: '/logs', name: 'logs', component: LogsView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})

createApp(App).use(createPinia()).use(router).mount('#app')
