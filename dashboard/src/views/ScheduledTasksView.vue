<script setup lang="ts">
import { AlarmClock, Clock3, Pencil, Play, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import {
  NButton, NDataTable, NEmpty, NForm, NFormItem, NInput, NInputNumber, NModal,
  NSelect, NSpin, NSwitch, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns, FormInst } from 'naive-ui'
import { computed, h, onMounted, ref } from 'vue'

import { api } from '../api'
import type { ScheduleKind, ScheduledTask, ScheduledTaskRequest, TaskRun } from '../types'
import PageHeader from '../components/layout/PageHeader.vue'

const message = useMessage()
const tasks = ref<ScheduledTask[]>([])
const handlers = ref<string[]>([])
const runs = ref<TaskRun[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const showForm = ref(false)
const showRuns = ref(false)
const editingId = ref<string | null>(null)
const deletingTask = ref<ScheduledTask | null>(null)
const togglingId = ref<string | null>(null)
const formRef = ref<FormInst | null>(null)

const form = ref<ScheduledTaskRequest>(emptyForm())
const kindOptions = [
  { label: 'Cron 表达式', value: 'cron' as ScheduleKind },
  { label: '固定间隔', value: 'interval' as ScheduleKind },
  { label: '单次执行', value: 'once' as ScheduleKind },
]
const handlerOptions = computed(() => handlers.value.map((value) => ({ label: value, value })))

function emptyForm(): ScheduledTaskRequest {
  return {
    name: '', handler_name: '', schedule_kind: 'cron', cron_expression: '*/30 * * * *',
    interval_seconds: 300, run_at: null, timezone: 'Asia/Shanghai', payload: {}, enabled: true,
  }
}
function format(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value))
}
function statusType(status: ScheduledTask['status']) {
  if (status === 'completed' || status === 'scheduled') return 'success'
  if (status === 'running') return 'info'
  if (status === 'disabled') return 'default'
  return 'error'
}
async function load() {
  loading.value = true; error.value = ''
  try { [tasks.value, handlers.value] = await Promise.all([api.scheduledTasks(), api.scheduledTaskHandlers()]) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '定时任务读取失败' }
  finally { loading.value = false }
}
function openCreate() { editingId.value = null; form.value = emptyForm(); showForm.value = true }
function openEdit(task: ScheduledTask) {
  editingId.value = task.task_id
  form.value = {
    name: task.name, handler_name: task.handler_name, schedule_kind: task.schedule_kind,
    cron_expression: task.cron_expression, interval_seconds: task.interval_seconds,
    run_at: task.run_at, timezone: task.timezone, payload: task.payload, enabled: task.enabled,
  }
  showForm.value = true
}
async function save() {
  await formRef.value?.validate()
  saving.value = true; error.value = ''
  try {
    const request: ScheduledTaskRequest = {
      ...form.value,
      cron_expression: form.value.schedule_kind === 'cron' ? form.value.cron_expression : null,
      interval_seconds: form.value.schedule_kind === 'interval' ? form.value.interval_seconds : null,
      run_at: form.value.schedule_kind === 'once' ? form.value.run_at : null,
    }
    if (editingId.value) await api.updateScheduledTask(editingId.value, request)
    else await api.createScheduledTask(request)
    showForm.value = false; message.success('定时任务已保存'); await load()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '保存失败' }
  finally { saving.value = false }
}
function remove(task: ScheduledTask) {
  deletingTask.value = task
}
async function deleteTask(task: ScheduledTask) {
  saving.value = true
  try {
    await api.deleteScheduledTask(task.task_id)
    deletingTask.value = null
    message.success('已删除')
    await load()
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败' }
  finally { saving.value = false }
}
async function runNow(task: ScheduledTask) {
  try { await api.runScheduledTask(task.task_id); message.success(`“${task.name}”已执行`); await load() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '执行失败' }
}
async function toggleTask(task: ScheduledTask, enabled: boolean) {
  togglingId.value = task.task_id
  error.value = ''
  try {
    await api.updateScheduledTask(task.task_id, {
      name: task.name,
      handler_name: task.handler_name,
      schedule_kind: task.schedule_kind,
      cron_expression: task.schedule_kind === 'cron' ? task.cron_expression : null,
      interval_seconds: task.schedule_kind === 'interval' ? task.interval_seconds : null,
      run_at: task.schedule_kind === 'once' ? task.run_at : null,
      timezone: task.timezone,
      payload: task.payload,
      enabled,
    })
    message.success(enabled ? '任务已启用' : '任务已停用')
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '状态更新失败'
  } finally {
    togglingId.value = null
  }
}
async function showHistory(task: ScheduledTask) {
  try { runs.value = await api.scheduledTaskRuns(task.task_id); showRuns.value = true }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '执行记录读取失败' }
}
const columns: DataTableColumns<ScheduledTask> = [
  { title: '任务', key: 'name', render: (row) => h('div', {}, [h('strong', {}, row.name), h('small', { class: 'task-subtitle' }, row.handler_name)]) },
  { title: '计划', key: 'schedule_kind', render: (row) => row.schedule_kind === 'cron' ? `Cron · ${row.cron_expression}` : row.schedule_kind === 'interval' ? `每 ${row.interval_seconds} 秒` : `一次 · ${format(row.run_at)}` },
  { title: '状态', key: 'status', render: (row) => h(NTag, { type: statusType(row.status), size: 'small' }, { default: () => row.status }) },
  { title: '启用', key: 'enabled', render: (row) => h(NSwitch, {
    value: row.enabled, loading: togglingId.value === row.task_id,
    onUpdateValue: (value: boolean) => void toggleTask(row, value),
  }) },
  { title: '下一次执行', key: 'next_run_at', render: (row) => format(row.next_run_at) },
  { title: '上次执行', key: 'last_run_at', render: (row) => format(row.last_run_at) },
  { title: '操作', key: 'actions', render: (row) => h('div', { class: 'task-actions' }, [
    h(NButton, { quaternary: true, circle: true, title: '立即执行', onClick: () => void runNow(row) }, { icon: () => h(Play, { size: 16 }) }),
    h(NButton, { quaternary: true, circle: true, title: '执行记录', onClick: () => void showHistory(row) }, { icon: () => h(Clock3, { size: 16 }) }),
    h(NButton, { quaternary: true, circle: true, title: '编辑', onClick: () => openEdit(row) }, { icon: () => h(Pencil, { size: 16 }) }),
    h(NButton, { quaternary: true, circle: true, type: 'error', title: '删除', onClick: () => remove(row) }, { icon: () => h(Trash2, { size: 16 }) }),
  ]) },
]
onMounted(load)
</script>

<template>
  <section class="page">
    <PageHeader title="定时任务" :description="`${tasks.length} 个持久化任务`">
      <NButton secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</NButton>
      <NButton type="primary" @click="openCreate"><template #icon><Plus :size="16" /></template>新建任务</NButton>
    </PageHeader>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="panel task-panel">
      <div v-if="loading" class="empty-state"><NSpin size="small" /> 正在读取定时任务…</div>
      <NEmpty v-else-if="tasks.length === 0" description="暂无定时任务" />
      <NDataTable v-else :columns="columns" :data="tasks" :bordered="false" />
    </div>

    <NModal v-model:show="showForm" preset="card" :title="editingId ? '编辑定时任务' : '新建定时任务'" style="width: min(620px, calc(100vw - 32px))">
      <NForm ref="formRef" :model="form" label-placement="top">
        <NFormItem label="任务名称" path="name" :rule="{ required: true, message: '请输入任务名称' }"><NInput v-model:value="form.name" /></NFormItem>
        <NFormItem label="处理器" path="handler_name" :rule="{ required: true, message: '请选择处理器' }"><NSelect v-model:value="form.handler_name" :options="handlerOptions" placeholder="选择已注册的处理器" /></NFormItem>
        <NFormItem label="调度类型"><NSelect v-model:value="form.schedule_kind" :options="kindOptions" /></NFormItem>
        <NFormItem v-if="form.schedule_kind === 'cron'" label="Cron 表达式"><NInput v-model:value="form.cron_expression" placeholder="例如：0 9 * * *" /></NFormItem>
        <NFormItem v-if="form.schedule_kind === 'interval'" label="间隔秒数"><NInputNumber v-model:value="form.interval_seconds" :min="1" :max="31536000" style="width: 100%" /></NFormItem>
        <NFormItem v-if="form.schedule_kind === 'once'" label="执行时间"><NInput v-model:value="form.run_at" placeholder="2026-08-19T18:00:00+08:00" /></NFormItem>
        <NFormItem label="时区"><NInput v-model:value="form.timezone" placeholder="Asia/Shanghai" /></NFormItem>
        <NFormItem label="Payload JSON"><NInput :value="JSON.stringify(form.payload, null, 2)" type="textarea" :autosize="{ minRows: 4, maxRows: 8 }" @update:value="(value) => { try { form.payload = JSON.parse(value) } catch { /* wait for valid JSON */ } }" /></NFormItem>
        <NFormItem label="启用任务"><NSwitch v-model:value="form.enabled" /></NFormItem>
      </NForm>
      <div class="form-actions"><NButton secondary @click="showForm = false">取消</NButton><NButton type="primary" :loading="saving" @click="save">保存</NButton></div>
    </NModal>
    <NModal v-model:show="showRuns" preset="card" title="执行记录" style="width: min(720px, calc(100vw - 32px))">
      <NEmpty v-if="runs.length === 0" description="暂无执行记录" />
      <NDataTable v-else :columns="[
        { title: '开始时间', key: 'started_at', render: (row: TaskRun) => format(row.started_at) },
        { title: '状态', key: 'status' }, { title: '耗时', key: 'duration_ms', render: (row: TaskRun) => row.duration_ms === null ? '—' : `${row.duration_ms} ms` },
        { title: '错误', key: 'error', render: (row: TaskRun) => row.error || '—' },
      ]" :data="runs" :bordered="false" />
    </NModal>
    <NModal :show="deletingTask !== null" preset="card" title="删除定时任务" style="width: min(420px, calc(100vw - 32px))" @update:show="(show) => { if (!show) deletingTask = null }">
      <p>确定删除“{{ deletingTask?.name }}”？任务定义和执行历史将一并删除。</p>
      <div class="form-actions">
        <NButton secondary @click="deletingTask = null">取消</NButton>
        <NButton v-if="deletingTask" type="error" :loading="saving" @click="deleteTask(deletingTask)">删除</NButton>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.task-panel { overflow: hidden; }
.task-subtitle { display: block; margin-top: 3px; color: #7c8681; font-size: 11px; }
.task-actions { display: flex; justify-content: flex-end; gap: 4px; }
</style>
