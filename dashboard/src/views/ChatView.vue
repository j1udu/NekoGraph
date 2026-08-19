<script setup lang="ts">
import { MessageSquarePlus, Send, Sparkles, Trash2 } from '@lucide/vue'
import { computed, nextTick, onMounted, ref } from 'vue'

import { api } from '../api'
import type { ConversationSummary, HistoryMessage } from '../types'

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
  messages.value.push({ role: 'user', content: text, tool_calls: [] })
  sending.value = true
  await scrollToBottom()
  try {
    const response = await api.send(activeConversationId.value, text)
    messages.value.push({ role: 'assistant', content: response.content, tool_calls: [] })
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

async function removeConversation(conversation: ConversationSummary) {
  if (sending.value) return
  if (!window.confirm(`删除对话“${conversation.title}”？`)) return
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
    <header class="page-header">
      <div>
        <h1>本地对话</h1>
        <p>{{ activeConversation?.title ?? '新对话' }}</p>
      </div>
      <button class="button primary" type="button" :disabled="sending" @click="newConversation">
        <MessageSquarePlus :size="16" /> 新建对话
      </button>
    </header>

    <div class="chat-layout">
      <aside class="conversation-list panel">
        <div class="conversation-list-header"><strong>对话</strong><span>{{ conversations.length }}</span></div>
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
          <button class="conversation-delete" type="button" title="删除对话" @click.stop="removeConversation(conversation)"><Trash2 :size="15" /></button>
        </div>
      </aside>
      <div class="chat-surface panel">
      <div ref="list" class="message-list">
        <div v-if="messages.length === 0" class="chat-empty">
          <div class="chat-empty-icon"><Sparkles :size="23" /></div>
          <strong>NekoGraph</strong>
        </div>
        <div
          v-for="(message, index) in messages"
          :key="index"
          class="message-row"
          :class="message.role"
        >
          <div class="message-label">{{ message.role === 'user' ? 'You' : message.role === 'tool' ? 'Tool' : 'NekoGraph' }}</div>
          <div class="message-bubble">{{ message.content }}</div>
        </div>
        <div v-if="sending" class="message-row assistant">
          <div class="message-label">NekoGraph</div>
          <div class="message-bubble pending">Thinking…</div>
        </div>
      </div>
      <div v-if="error" class="chat-error">{{ error }}</div>
      <div class="chat-composer">
        <textarea
          v-model="input"
          rows="2"
          placeholder="输入消息"
          :disabled="sending"
          @keydown="onKeydown"
        />
        <button class="send-button" type="button" title="发送" :disabled="!input.trim() || sending" @click="send">
          <Send :size="18" />
        </button>
      </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-page { height: 100vh; display: flex; flex-direction: column; padding-bottom: 28px; }
.chat-layout { min-height: 0; flex: 1; display: grid; grid-template-columns: 238px minmax(0, 1fr); gap: 16px; }
.conversation-list { min-height: 0; overflow-y: auto; padding: 12px; }
.conversation-list-header { padding: 4px 5px 12px; display: flex; justify-content: space-between; color: #52605a; font-size: 13px; }
.conversation-list-header span { color: #89948f; }
.conversation-item { width: 100%; min-height: 58px; border: 1px solid transparent; border-radius: 7px; padding: 9px 8px; display: flex; align-items: center; gap: 7px; text-align: left; background: transparent; color: #2b3430; }
.conversation-item:hover { background: #f0f4f1; }
.conversation-item.selected { border-color: #b8d7c7; background: #e7f3ec; }
.conversation-item-copy { min-width: 0; flex: 1; }
.conversation-item-copy strong, .conversation-item-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-item-copy strong { font-size: 13px; }
.conversation-item-copy small { margin-top: 4px; color: #81908a; font-size: 11px; }
.conversation-delete { width: 28px; height: 28px; display: grid; place-items: center; border-radius: 6px; color: #9a6767; }
.conversation-delete:hover { background: #f9e7e7; color: #a33c3c; }
.chat-surface { min-height: 0; flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.message-list { flex: 1; overflow-y: auto; padding: 26px max(20px, 7%); }
.chat-empty { height: 100%; min-height: 260px; display: grid; place-content: center; justify-items: center; gap: 10px; color: #62706a; }
.chat-empty-icon { width: 48px; height: 48px; display: grid; place-items: center; border-radius: 8px; background: #dff2e8; color: #176447; }
.message-row { max-width: 780px; margin: 0 auto 22px; }
.message-label { margin-bottom: 7px; color: #6f7974; font-size: 11px; font-weight: 700; }
.message-row.user .message-label { text-align: right; }
.message-bubble { width: fit-content; max-width: min(82%, 680px); padding: 12px 14px; border-radius: 8px; background: #edf1ef; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; font-size: 14px; }
.message-row.user .message-bubble { margin-left: auto; background: #1d7257; color: #fff; }
.message-row.tool .message-bubble { background: #fff2d9; border: 1px solid #ebd5aa; }
.message-bubble.pending { color: #71807a; }
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
