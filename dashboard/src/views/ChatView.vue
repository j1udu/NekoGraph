<script setup lang="ts">
import { RotateCcw, Send, Sparkles } from '@lucide/vue'
import { nextTick, onMounted, ref } from 'vue'

import { api } from '../api'
import type { HistoryMessage } from '../types'

const conversationId = (() => {
  const stored = localStorage.getItem('nekograph.web.conversation')
  if (stored) return stored
  const created = `browser-${crypto.randomUUID()}`
  localStorage.setItem('nekograph.web.conversation', created)
  return created
})()

const messages = ref<HistoryMessage[]>([])
const input = ref('')
const sending = ref(false)
const error = ref('')
const list = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
}

async function loadHistory() {
  try {
    messages.value = await api.history(conversationId)
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
    const response = await api.send(conversationId, text)
    messages.value.push({ role: 'assistant', content: response.content, tool_calls: [] })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '发送失败'
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

async function reset() {
  if (sending.value) return
  error.value = ''
  try {
    await api.reset(conversationId)
    messages.value = []
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '重置失败'
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

onMounted(loadHistory)
</script>

<template>
  <section class="page chat-page">
    <header class="page-header">
      <div>
        <h1>本地对话</h1>
        <p>{{ conversationId }}</p>
      </div>
      <button class="button secondary" type="button" :disabled="sending" @click="reset">
        <RotateCcw :size="16" /> 重置上下文
      </button>
    </header>

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
  </section>
</template>

<style scoped>
.chat-page { height: 100vh; display: flex; flex-direction: column; padding-bottom: 28px; }
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
@media (max-width: 620px) { .message-list { padding: 20px 12px; } .message-bubble { max-width: 90%; } }
</style>
