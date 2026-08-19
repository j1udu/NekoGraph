<script setup lang="ts">
import { Check, FileJson, Plus, Power, Trash2, X } from '@lucide/vue'
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import type { ModelProfile, ModelProfileInput, RuntimeStatus } from '../types'

const profiles = ref<ModelProfile[]>([])
const status = ref<RuntimeStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const dialog = ref<'single' | 'bulk' | null>(null)
const bulkJson = ref('[\n  {\n    "name": "Primary",\n    "model": "model-id",\n    "base_url": "https://api.example.com/v1",\n    "api_key": "",\n    "temperature": 0,\n    "timeout_seconds": 30\n  }\n]')
const form = reactive<ModelProfileInput>({
  name: '', model: '', base_url: '', api_key: '', temperature: 0, timeout_seconds: 30,
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [items, runtime] = await Promise.all([api.models(), api.status()])
    profiles.value = items
    status.value = runtime
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function closeDialog() {
  dialog.value = null
  error.value = ''
}

async function submitProfiles(items: ModelProfileInput[]) {
  saving.value = true
  error.value = ''
  try {
    await api.importModels(items)
    closeDialog()
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导入失败'
  } finally {
    saving.value = false
  }
}

async function submitSingle() {
  await submitProfiles([{ ...form }])
  Object.assign(form, { name: '', model: '', base_url: '', api_key: '', temperature: 0, timeout_seconds: 30 })
}

async function submitBulk() {
  try {
    const parsed = JSON.parse(bulkJson.value) as unknown
    if (!Array.isArray(parsed)) throw new Error('JSON 顶层必须是数组')
    await submitProfiles(parsed as ModelProfileInput[])
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'JSON 解析失败'
  }
}

async function activate(profileId: string) {
  error.value = ''
  try {
    await api.activateModel(profileId)
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '切换失败'
  }
}

async function useEnvironment() {
  error.value = ''
  try {
    await api.activateEnvironment()
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '切换失败'
  }
}

async function remove(profile: ModelProfile) {
  if (!window.confirm(`删除模型配置“${profile.name}”？`)) return
  error.value = ''
  try {
    await api.deleteModel(profile.profile_id)
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>模型配置</h1>
        <p>{{ status?.model.name ?? '读取中' }} · {{ status?.model.model ?? '—' }}</p>
      </div>
      <div class="page-actions">
        <button class="button secondary" type="button" @click="dialog = 'bulk'">
          <FileJson :size="16" /> 批量导入
        </button>
        <button class="button primary" type="button" @click="dialog = 'single'">
          <Plus :size="16" /> 添加模型
        </button>
      </div>
    </header>

    <div v-if="error && !dialog" class="error-banner">{{ error }}</div>

    <div class="environment-row panel">
      <div>
        <span class="environment-label">环境配置</span>
        <strong>{{ status?.model.source === 'environment' ? status.model.model : 'Environment fallback' }}</strong>
      </div>
      <span v-if="status?.model.source === 'environment'" class="badge active"><Check :size="13" /> 当前使用</span>
      <button v-else class="button secondary" type="button" @click="useEnvironment"><Power :size="15" /> 启用</button>
    </div>

    <div class="panel models-panel">
      <div class="panel-header"><h2>已导入模型</h2><span>{{ profiles.length }}</span></div>
      <div v-if="loading" class="empty-state">正在读取模型配置…</div>
      <div v-else-if="profiles.length === 0" class="empty-state">尚未导入模型</div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead><tr><th>名称</th><th>模型 ID</th><th>Endpoint</th><th>参数</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr v-for="profile in profiles" :key="profile.profile_id">
              <td><div class="table-title">{{ profile.name }}</div><div class="table-subtitle">{{ profile.profile_id.slice(0, 10) }}</div></td>
              <td>{{ profile.model }}</td>
              <td><div class="endpoint">{{ profile.base_url }}</div></td>
              <td><div>T {{ profile.temperature }}</div><div class="table-subtitle">{{ profile.timeout_seconds }}s timeout</div></td>
              <td><span class="badge" :class="profile.active ? 'active' : 'inactive'">{{ profile.active ? 'Active' : 'Standby' }}</span></td>
              <td>
                <div class="row-actions">
                  <button v-if="!profile.active" class="icon-button" type="button" title="激活模型" @click="activate(profile.profile_id)"><Power :size="16" /></button>
                  <button class="icon-button danger" type="button" title="删除模型" :disabled="profile.active" @click="remove(profile)"><Trash2 :size="16" /></button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="dialog" class="modal-backdrop" @click.self="closeDialog">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>{{ dialog === 'single' ? '添加模型' : '批量导入模型' }}</h2>
          <button class="icon-button" type="button" title="关闭" @click="closeDialog"><X :size="18" /></button>
        </div>
        <div class="modal-body">
          <div v-if="error" class="error-banner">{{ error }}</div>
          <form v-if="dialog === 'single'" @submit.prevent="submitSingle">
            <div class="form-grid">
              <div class="field"><label for="model-name">显示名称</label><input id="model-name" v-model="form.name" required /></div>
              <div class="field"><label for="model-id">模型 ID</label><input id="model-id" v-model="form.model" required /></div>
              <div class="field full"><label for="base-url">Base URL</label><input id="base-url" v-model="form.base_url" type="url" required /></div>
              <div class="field full"><label for="api-key">API Key</label><input id="api-key" v-model="form.api_key" type="password" autocomplete="off" required /></div>
              <div class="field"><label for="temperature">Temperature</label><input id="temperature" v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" required /></div>
              <div class="field"><label for="timeout">Timeout (s)</label><input id="timeout" v-model.number="form.timeout_seconds" type="number" min="1" max="600" required /></div>
            </div>
            <div class="form-actions"><button class="button secondary" type="button" @click="closeDialog">取消</button><button class="button primary" type="submit" :disabled="saving">保存</button></div>
          </form>
          <form v-else @submit.prevent="submitBulk">
            <div class="field full"><label for="bulk-json">JSON Profiles</label><textarea id="bulk-json" v-model="bulkJson" spellcheck="false" required /></div>
            <div class="form-actions"><button class="button secondary" type="button" @click="closeDialog">取消</button><button class="button primary" type="submit" :disabled="saving">导入</button></div>
          </form>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.environment-row { min-height: 74px; padding: 13px 17px; margin-bottom: 16px; display: flex; align-items: center; gap: 14px; }
.environment-row > div { flex: 1; min-width: 0; }
.environment-row strong { display: block; margin-top: 4px; overflow-wrap: anywhere; }
.environment-label { color: #78827d; font-size: 11px; }
.models-panel { margin-top: 0; }
.models-panel .data-table { min-width: 900px; }
.endpoint { max-width: 310px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
