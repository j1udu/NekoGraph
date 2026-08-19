<script setup lang="ts">
import { MessageSquarePlus, Pencil, Send, Trash2 } from '@lucide/vue'
import { NButton, NInput, NModal, NPopconfirm } from 'naive-ui'
import { computed, nextTick, onMounted, ref } from 'vue'

import { api } from '../api'
import type { ConversationSummary, HistoryMessage } from '../types'
import nekoGraphLogo from '../assets/nekograph-logo.png'

const storageKey = 'nekograph.web.conversations'
const activeKey = 'nekograph.web.active-conversation'

function createConversation(): ConversationSummary {
  const now = new Date().toISOString()
  return { id: `browser-${crypto.randomUUID()}`, title: '新对话', created_at: now }
}

function loadConversations(): ConversationSummary[] {
  try {
    const stored = localStorage.getItem(storageKey)
    const legacy = localStorage.getItem('nekograph.web.conversation')
    const parsed = JSON.parse(stored ?? (legacy ? JSON.stringify([{
      id: legacy,
      title: '旧对话',
      created_at: new Date().toISOString(),
    }]) : '[]')) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is ConversationSummary => {
      if (!item || typeof item !== 'object') return false
      const value = item as Record<string, unknown>
      return typeof value.id === 'string' && typeof value.title === 'string' && typeof value.created_at === 'string'
    })
  } catch {
    return []
  }
}

const conversations = ref<ConversationSummary[]>(loadConversations())
if (conversations.value.length === 0) conversations.value = [createConversation()]
const storedActive = localStorage.getItem(activeKey)
const activeConversationId = ref(
  conversations.value.some((item) => item.id === storedActive) ? storedActive! : conversations.value[0].id,
)
const activeConversation = computed(() => conversations.value.find((item) => item.id === activeConversationId.value) ?? conversations.value[0])

const messages = ref<HistoryMessage[]>([])
const input = ref('')
const sending = ref(false)
const error = ref('')
const list = ref<HTMLElement | null>(null)
const renamingConversation = ref<ConversationSummary | null>(null)
const renameTitle = ref('')

function persistConversations() {
  localStorage.setItem(storageKey, JSON.stringify(conversations.value))
  localStorage.setItem(activeKey, activeConversationId.value)
}

async function scrollToBottom() {
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
}

async function loadHistory() {
  try {
    messages.value = await api.history(activeConversationId.value)
    await scrollToBottom()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取对话'
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  input.value = ''
  error.value = ''
  messages.value.push({ role: 'user', content: text, tool_calls: [], response_time_ms: null })
  sending.value = true
  await scrollToBottom()
  try {
    const response = await api.send(activeConversationId.value, text)
    messages.value.push({
      role: 'assistant',
      content: response.content,
      tool_calls: [],
      response_time_ms: response.response_time_ms,
    })
    const conversation = activeConversation.value
    if (conversation && conversation.title === '新对话') {
      conversation.title = text.slice(0, 28)
      persistConversations()
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '发送失败'
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

async function selectConversation(id: string) {
  if (sending.value || id === activeConversationId.value) return
  activeConversationId.value = id
  messages.value = []
  error.value = ''
  persistConversations()
  await loadHistory()
}

function newConversation() {
  if (sending.value) return
  const conversation = createConversation()
  conversations.value.unshift(conversation)
  activeConversationId.value = conversation.id
  messages.value = []
  error.value = ''
  persistConversations()
}

function renameConversation(conversation: ConversationSummary) {
  if (sending.value) return
  renamingConversation.value = conversation
  renameTitle.value = conversation.title
}

function submitRename() {
  const conversation = renamingConversation.value
  const title = renameTitle.value.trim()
  if (!conversation || !title) return
  conversation.title = title.slice(0, 80)
  persistConversations()
  renamingConversation.value = null
}

async function removeConversation(conversation: ConversationSummary) {
  if (sending.value) return
  error.value = ''
  try {
    await api.deleteConversation(conversation.id)
    const index = conversations.value.findIndex((item) => item.id === conversation.id)
    conversations.value = conversations.value.filter((item) => item.id !== conversation.id)
    if (conversation.id === activeConversationId.value) {
      const replacement = conversations.value[Math.max(0, index - 1)] ?? createConversation()
      if (conversations.value.length === 0) conversations.value = [replacement]
      activeConversationId.value = replacement.id
      messages.value = []
      await loadHistory()
    }
    persistConversations()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除失败'
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

onMounted(() => {
  persistConversations()
  void loadHistory()
})
</script>

<template>
  <section class="page chat-page">
    <div class="chat-layout">
      <aside class="conversation-list panel">
        <div class="conversation-list-header">
          <strong>对话 <span>{{ conversations.length }}</span></strong>
          <NButton type="primary" size="small" :disabled="sending" @click="newConversation">
            <template #icon><MessageSquarePlus :size="15" /></template>新建
          </NButton>
        </div>
        <div
          v-for="conversation in conversations"
          :key="conversation.id"
          class="conversation-item"
          :class="{ selected: conversation.id === activeConversationId }"
          role="button"
          tabindex="0"
          @click="selectConversation(conversation.id)"
          @keydown.enter="selectConversation(conversation.id)"
          @keydown.space.prevent="selectConversation(conversation.id)"
        >
          <span class="conversation-item-copy"><strong>{{ conversation.title }}</strong><small>{{ new Date(conversation.created_at).toLocaleDateString() }}</small></span>
          <NButton quaternary circle class="conversation-rename" title="重命名对话" @click.stop="renameConversation(conversation)"><template #icon><Pencil :size="15" /></template></NButton>
          <NPopconfirm @positive-click="removeConversation(conversation)"><template #trigger><NButton quaternary circle type="error" class="conversation-delete" title="删除对话" @click.stop><template #icon><Trash2 :size="15" /></template></NButton></template>删除对话“{{ conversation.title }}”？</NPopconfirm>
        </div>
      </aside>
      <div class="chat-surface panel">
      <div ref="list" class="message-list">
        <div v-if="messages.length === 0" class="chat-empty">
          <img class="chat-empty-logo" :src="nekoGraphLogo" alt="NekoGraph" />
        </div>
        <div
          v-for="(message, index) in messages"
          :key="index"
          class="message-row"
          :class="message.role"
        >
          <div class="message-label">{{ message.role === 'user' ? 'You' : message.role === 'tool' ? 'Tool' : 'NekoGraph' }}</div>
          <div class="message-bubble">{{ message.content }}</div>
          <div v-if="message.role === 'assistant' && message.response_time_ms !== null" class="message-meta">响应耗时 {{ message.response_time_ms }} ms</div>
        </div>
        <div v-if="sending" class="message-row assistant">
          <div class="message-label">NekoGraph</div>
          <div class="message-bubble pending">Thinking…</div>
        </div>
      </div>
      <div v-if="error" class="chat-error">{{ error }}</div>
      <div class="chat-composer">
        <NInput v-model:value="input" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" placeholder="输入消息，Enter 发送，Shift+Enter 换行" :disabled="sending" @keydown="onKeydown" />
        <NButton type="primary" class="send-button" title="发送" :disabled="!input.trim() || sending" :loading="sending" @click="send"><template #icon><Send :size="18" /></template></NButton>
      </div>
      </div>
    </div>
    <NModal :show="renamingConversation !== null" preset="card" title="重命名对话" style="width: min(440px, calc(100vw - 32px))" @update:show="(show) => !show && (renamingConversation = null)">
      <form @submit.prevent="submitRename">
        <NInput v-model:value="renameTitle" maxlength="80" show-count autofocus placeholder="输入新的对话名称" />
        <div class="form-actions"><NButton secondary @click="renamingConversation = null">取消</NButton><NButton type="primary" attr-type="submit" :disabled="!renameTitle.trim()">保存</NButton></div>
      </form>
    </NModal>
  </section>
</template>

<style scoped>
.chat-page { height: 100vh; display: flex; flex-direction: column; padding-bottom: 28px; }
.chat-layout { min-height: 0; flex: 1; display: grid; grid-template-columns: 238px minmax(0, 1fr); gap: 16px; }
.conversation-list { min-height: 0; overflow-y: auto; padding: 12px; }
.conversation-list-header { padding: 0 0 12px; display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #52605a; font-size: 13px; }
.conversation-list-header strong { display: inline-flex; align-items: center; gap: 5px; }
.conversation-list-header strong span { color: #89948f; font-weight: 500; }
.conversation-item { width: 100%; min-height: 58px; border: 1px solid transparent; border-radius: 7px; padding: 9px 8px; display: flex; align-items: center; gap: 7px; text-align: left; background: transparent; color: #2b3430; }
.conversation-item:hover { background: #f0f4f1; }
.conversation-item.selected { border-color: #b8d7c7; background: #e7f3ec; }
.conversation-item-copy { min-width: 0; flex: 1; }
.conversation-item-copy strong, .conversation-item-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-item-copy strong { font-size: 13px; }
.conversation-item-copy small { margin-top: 4px; color: #81908a; font-size: 11px; }
.conversation-delete { width: 28px; height: 28px; display: grid; place-items: center; border-radius: 6px; color: #9a6767; }
.conversation-delete:hover { background: #f9e7e7; color: #a33c3c; }
.conversation-rename { width: 28px; height: 28px; display: grid; place-items: center; border: 0; border-radius: 6px; background: transparent; color: #6b8176; }
.conversation-rename:hover { background: #e1eee7; color: #176447; }
.chat-surface { min-height: 0; flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.message-list { flex: 1; overflow-y: auto; padding: 26px max(20px, 7%); }
.chat-empty { height: 100%; min-height: 260px; display: grid; place-content: center; justify-items: center; gap: 10px; color: #62706a; }
.chat-empty-logo { width: min(190px, 48vw); height: auto; object-fit: contain; }
.message-row { max-width: 780px; margin: 0 auto 22px; }
.message-label { margin-bottom: 7px; color: #6f7974; font-size: 11px; font-weight: 700; }
.message-row.user .message-label { text-align: right; }
.message-bubble { width: fit-content; max-width: min(82%, 680px); padding: 12px 14px; border-radius: 8px; background: #edf1ef; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; font-size: 14px; }
.message-row.user .message-bubble { margin-left: auto; background: #1d7257; color: #fff; }
.message-row.tool .message-bubble { background: #fff2d9; border: 1px solid #ebd5aa; }
.message-bubble.pending { color: #71807a; }
.message-meta { margin-top: 5px; color: #87938d; font-size: 11px; }
.chat-error { padding: 9px 18px; color: #943d3d; background: #fff0f0; border-top: 1px solid #eccaca; font-size: 12px; }
.chat-composer { min-height: 84px; padding: 14px; border-top: 1px solid #e1e6e3; display: grid; grid-template-columns: 1fr 42px; gap: 10px; align-items: end; }
.chat-composer textarea { width: 100%; min-height: 54px; max-height: 150px; resize: vertical; border: 1px solid #cfd7d3; border-radius: 7px; padding: 10px 12px; outline: none; }
.chat-composer textarea:focus { border-color: #278260; box-shadow: 0 0 0 3px rgba(39, 130, 96, .1); }
.send-button { width: 42px; height: 42px; border: 0; border-radius: 7px; display: grid; place-items: center; color: #fff; background: #176c52; }
.send-button:disabled { opacity: .5; cursor: not-allowed; }
@media (max-width: 900px) { .chat-page { height: 100dvh; } }
@media (max-width: 760px) { .chat-layout { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); } .conversation-list { max-height: 150px; } .conversation-item { display: inline-flex; width: calc(50% - 5px); margin-right: 5px; } }
@media (max-width: 620px) { .message-list { padding: 20px 12px; } .message-bubble { max-width: 90%; } .conversation-item { width: 100%; margin-right: 0; } }
</style>
