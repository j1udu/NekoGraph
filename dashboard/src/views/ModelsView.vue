<script setup lang="ts">
import { Check, FileJson, Pencil, Plus, Power, Trash2, Zap } from '@lucide/vue'
import { NButton, NEmpty, NForm, NFormItem, NInput, NInputNumber, NModal, NPopconfirm, NSpin, NSpace, NTable, NTag } from 'naive-ui'
import { storeToRefs } from 'pinia'
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import type { ModelProfile, ModelProfileInput } from '../types'
import PageHeader from '../components/layout/PageHeader.vue'
import { useModelStore } from '../stores/models'
import { useRuntimeStore } from '../stores/runtime'

const models = useModelStore()
const runtime = useRuntimeStore()
const { profiles, loading } = storeToRefs(models)
const { status } = storeToRefs(runtime)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const testingProfileId = ref<string | null>(null)
const dialog = ref<'single' | 'edit' | 'bulk' | null>(null)
const editingProfileId = ref<string | null>(null)
const bulkJson = ref('[\n  {\n    "name": "Primary",\n    "model": "model-id",\n    "base_url": "https://api.example.com/v1",\n    "api_key": "",\n    "temperature": 0,\n    "timeout_seconds": 30\n  }\n]')
const form = reactive<ModelProfileInput>({
  name: '', model: '', base_url: '', api_key: '', temperature: 0, timeout_seconds: 30,
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([models.refresh(), runtime.refresh()])
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function closeDialog() {
  dialog.value = null
  editingProfileId.value = null
  error.value = ''
  notice.value = ''
}

function resetForm() {
  Object.assign(form, { name: '', model: '', base_url: '', api_key: '', temperature: 0, timeout_seconds: 30 })
}

function openCreate() {
  resetForm()
  dialog.value = 'single'
}

function openEdit(profile: ModelProfile) {
  Object.assign(form, {
    name: profile.name,
    model: profile.model,
    base_url: profile.base_url,
    api_key: '',
    temperature: profile.temperature,
    timeout_seconds: profile.timeout_seconds,
  })
  editingProfileId.value = profile.profile_id
  dialog.value = 'edit'
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
  if (dialog.value === 'edit' && editingProfileId.value) {
    saving.value = true
    error.value = ''
    try {
      const { api_key, ...values } = form
      await api.updateModel(editingProfileId.value, {
        ...values,
        ...(api_key ? { api_key } : {}),
      })
      closeDialog()
      await load()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '保存失败'
    } finally {
      saving.value = false
    }
    return
  }
  await submitProfiles([{ ...form }])
  resetForm()
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
  notice.value = ''
  try {
    await api.activateModel(profileId)
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '切换失败'
  }
}

async function testConnection(profile: ModelProfile) {
  error.value = ''
  notice.value = ''
  testingProfileId.value = profile.profile_id
  try {
    await api.testModel(profile.profile_id)
    notice.value = `“${profile.name}”连接成功`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '连接测试失败'
  } finally {
    testingProfileId.value = null
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
    <PageHeader title="模型配置" :description="`${status?.model.name ?? '读取中'} · ${status?.model.model ?? '—'}`">
      <NButton secondary @click="dialog = 'bulk'"><template #icon><FileJson :size="16" /></template>批量导入</NButton>
      <NButton type="primary" @click="openCreate"><template #icon><Plus :size="16" /></template>添加模型</NButton>
    </PageHeader>

    <div v-if="error && !dialog" class="error-banner">{{ error }}</div>
    <div v-if="notice && !dialog" class="success-banner">{{ notice }}</div>

    <div class="environment-row panel">
      <div>
        <span class="environment-label">环境配置</span>
        <strong>{{ status?.model.source === 'environment' ? status.model.model : 'Environment fallback' }}</strong>
      </div>
      <NTag v-if="status?.model.source === 'environment'" type="success"><Check :size="13" /> 当前使用</NTag>
      <NButton v-else secondary @click="useEnvironment"><template #icon><Power :size="15" /></template>启用</NButton>
    </div>

    <div class="panel models-panel">
      <div class="panel-header"><h2>已导入模型</h2><span>{{ profiles.length }}</span></div>
      <div v-if="loading" class="empty-state"><NSpin size="small" /> 正在读取模型配置…</div>
      <NEmpty v-else-if="profiles.length === 0" description="尚未导入模型" />
      <div v-else class="table-wrap">
        <NTable :single-line="false" striped>
          <thead><tr><th>名称</th><th>模型 ID</th><th>Endpoint</th><th>参数</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr v-for="profile in profiles" :key="profile.profile_id">
              <td><div class="table-title">{{ profile.name }}</div><div class="table-subtitle">{{ profile.profile_id.slice(0, 10) }}</div></td>
              <td>{{ profile.model }}</td>
              <td><div class="endpoint">{{ profile.base_url }}</div></td>
              <td><div>T {{ profile.temperature }}</div><div class="table-subtitle">{{ profile.timeout_seconds }}s timeout</div></td>
              <td><NTag :type="profile.active ? 'success' : 'default'">{{ profile.active ? '当前使用' : '待机' }}</NTag></td>
              <td>
                <div class="row-actions">
                  <NButton v-if="!profile.active" quaternary circle title="激活模型" @click="activate(profile.profile_id)"><template #icon><Power :size="16" /></template></NButton>
                  <NButton quaternary circle title="测试连接" :loading="testingProfileId === profile.profile_id" :disabled="testingProfileId !== null" @click="testConnection(profile)"><template #icon><Zap :size="16" /></template></NButton>
                  <NButton quaternary circle title="编辑模型" @click="openEdit(profile)"><template #icon><Pencil :size="16" /></template></NButton>
                  <NPopconfirm v-if="!profile.active" @positive-click="remove(profile)"><template #trigger><NButton quaternary circle type="error" title="删除模型"><template #icon><Trash2 :size="16" /></template></NButton></template>删除模型配置“{{ profile.name }}”？</NPopconfirm>
                </div>
              </td>
            </tr>
          </tbody>
        </NTable>
      </div>
    </div>

    <NModal :show="dialog !== null" preset="card" style="width: min(620px, calc(100vw - 32px))" :title="dialog === 'single' ? '添加模型' : dialog === 'edit' ? '编辑模型' : '批量导入模型'" @update:show="(show) => !show && closeDialog()">
        <div>
          <div v-if="error" class="error-banner">{{ error }}</div>
          <form v-if="dialog === 'single' || dialog === 'edit'" @submit.prevent="submitSingle">
            <NForm label-placement="top"><NFormItem label="显示名称" required><NInput v-model:value="form.name" placeholder="例如：主力模型" /></NFormItem><NFormItem label="模型 ID" required><NInput v-model:value="form.model" placeholder="填写服务商提供的模型标识" /></NFormItem><NFormItem label="Base URL" required><NInput v-model:value="form.base_url" placeholder="例如：https://api.example.com/v1" /></NFormItem><NFormItem :label="`API Key${dialog === 'edit' ? '（留空则保留）' : ''}`" :required="dialog === 'single'"><NInput v-model:value="form.api_key" type="password" show-password-on="click" placeholder="不会在页面中回显已保存的密钥" /></NFormItem><NSpace><NFormItem label="Temperature" required><NInputNumber v-model:value="form.temperature" :min="0" :max="2" :step="0.1" /></NFormItem><NFormItem label="超时（秒）" required><NInputNumber v-model:value="form.timeout_seconds" :min="1" :max="600" /></NFormItem></NSpace></NForm>
            <div class="form-actions"><NButton secondary @click="closeDialog">取消</NButton><NButton type="primary" attr-type="submit" :loading="saving">保存</NButton></div>
          </form>
          <form v-else @submit.prevent="submitBulk">
            <NFormItem label="模型配置 JSON" required><NInput v-model:value="bulkJson" type="textarea" :autosize="{ minRows: 10, maxRows: 20 }" /></NFormItem>
            <div class="form-actions"><NButton secondary @click="closeDialog">取消</NButton><NButton type="primary" attr-type="submit" :loading="saving">导入</NButton></div>
          </form>
        </div>
    </NModal>
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
