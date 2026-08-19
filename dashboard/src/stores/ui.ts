import { defineStore } from 'pinia'
import { ref } from 'vue'

export type NoticeType = 'success' | 'error' | 'warning' | 'info'

export const useUiStore = defineStore('ui', () => {
  const sidebarOpen = ref(false)
  const notice = ref<{ id: number; type: NoticeType; content: string } | null>(null)
  let nextNoticeId = 0

  function closeSidebar() { sidebarOpen.value = false }
  function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value }
  function showNotice(type: NoticeType, content: string) {
    notice.value = { id: ++nextNoticeId, type, content }
  }
  function clearNotice() { notice.value = null }

  return { sidebarOpen, notice, closeSidebar, toggleSidebar, showNotice, clearNotice }
})
