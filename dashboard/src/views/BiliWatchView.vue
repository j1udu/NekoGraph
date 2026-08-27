<script setup lang="ts">
import { BellRing, Pencil, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import {
  NButton, NCard, NDataTable, NEmpty, NForm, NFormItem, NInput, NInputNumber,
  NModal, NSpace, NSwitch, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, h, onMounted, ref } from 'vue'

import { api } from '../api'
import type {
  BiliWatchConfig, BiliWatchConfigUpdate, BiliWatchDelivery,
  BiliWatchSubscription, BiliWatchSubscriptionRequest,
} from '../types'
import PageHeader from '../components/layout/PageHeader.vue'

const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const config = ref<BiliWatchConfig | null>(null)
const subscriptions = ref<BiliWatchSubscription[]>([])
const deliveries = ref<BiliWatchDelivery[]>([])
const showForm = ref(false)
const editing = ref<BiliWatchSubscription | null>(null)
const form = ref<BiliWatchSubscriptionRequest>(emptySubscription())
const adminsText = ref('')
const pollInterval = ref(30)
const sessdata = ref('')
const biliJct = ref('')
const dedeUserId = ref('')
const botOptions = ref<string[]>([])

function emptySubscription(): BiliWatchSubscriptionRequest {
  return {
    bot_id: '', group_id: '', uid: '', watch_dynamic: true, watch_live: true,
    at_all_dynamic: false, at_all_live: false, filter_forward: false, enabled: true,
  }
}
function format(value: string | null) {
  return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '—'
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const [loadedConfig, loadedSubscriptions, loadedDeliveries, bots] = await Promise.all([
      api.biliWatchConfig(), api.biliWatchSubscriptions(), api.biliWatchDeliveries(50), api.onebotBots(),
    ])
    config.value = loadedConfig; subscriptions.value = loadedSubscriptions; deliveries.value = loadedDeliveries
    botOptions.value = bots.map((bot) => bot.bot_id)
    adminsText.value = loadedConfig.admins.join(', ')
    pollInterval.value = loadedConfig.poll_interval_seconds
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'BiliWatch 数据读取失败' }
  finally { loading.value = false }
}
function openCreate() { editing.value = null; form.value = { ...emptySubscription(), bot_id: botOptions.value[0] ?? '' }; showForm.value = true }
function openEdit(item: BiliWatchSubscription) {
  editing.value = item
  form.value = {
    bot_id: item.bot_id, group_id: item.group_id, uid: item.uid, watch_dynamic: item.watch_dynamic,
    watch_live: item.watch_live, at_all_dynamic: item.at_all_dynamic, at_all_live: item.at_all_live,
    filter_forward: item.filter_forward, enabled: item.enabled,
  }
  showForm.value = true
}
async function saveSubscription() {
  saving.value = true
  try {
    if (editing.value) await api.updateBiliWatchSubscription(editing.value.subscription_id, form.value)
    else await api.createBiliWatchSubscription(form.value)
    showForm.value = false; message.success('BiliWatch 订阅已保存'); await load()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '订阅保存失败' }
  finally { saving.value = false }
}
async function remove(item: BiliWatchSubscription) {
  try { await api.deleteBiliWatchSubscription(item.subscription_id); message.success('订阅已删除'); await load() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '订阅删除失败' }
}
async function saveConfig() {
  saving.value = true
  try {
    const payload: BiliWatchConfigUpdate = {
      admins: adminsText.value.split(',').map((item) => item.trim()).filter(Boolean),
      poll_interval_seconds: pollInterval.value,
      sessdata: sessdata.value || null, bili_jct: biliJct.value || null, dede_user_id: dedeUserId.value || null,
    }
    config.value = await api.updateBiliWatchConfig(payload)
    sessdata.value = ''; biliJct.value = ''; dedeUserId.value = ''
    message.success('BiliWatch 配置已保存'); await load()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'BiliWatch 配置保存失败' }
  finally { saving.value = false }
}
async function testCookie() {
  try { await api.testBiliWatchCookie(); message.success('B 站 Cookie 测试成功') }
  catch (reason) { error.value = reason instanceof Error ? reason.message : 'Cookie 测试失败' }
}
const subscriptionColumns: DataTableColumns<BiliWatchSubscription> = [
  { title: '群 / UP 主', key: 'target', render: (row) => h('div', {}, [h('strong', {}, `群 ${row.group_id}`), h('small', { class: 'table-subtitle' }, `${row.uname} · ${row.uid}`)]) },
  { title: '监测内容', key: 'watch', render: (row) => [row.watch_dynamic ? `动态${row.at_all_dynamic ? ' @全体' : ''}` : '', row.watch_live ? `直播${row.at_all_live ? ' @全体' : ''}` : ''].filter(Boolean).join(' / ') || '未启用' },
  { title: '策略', key: 'filter_forward', render: (row) => row.filter_forward ? '屏蔽转发' : '接收转发' },
  { title: '状态', key: 'enabled', render: (row) => h(NTag, { size: 'small', type: row.enabled ? 'success' : 'default' }, { default: () => row.enabled ? '启用' : '停用' }) },
  { title: '操作', key: 'actions', render: (row) => h(NSpace, { justify: 'end', size: 'small' }, { default: () => [
    h(NButton, { quaternary: true, circle: true, title: '编辑', onClick: () => openEdit(row) }, { icon: () => h(Pencil, { size: 16 }) }),
    h(NButton, { quaternary: true, circle: true, type: 'error', title: '删除', onClick: () => void remove(row) }, { icon: () => h(Trash2, { size: 16 }) }),
  ] }) },
]
const deliveryColumns: DataTableColumns<BiliWatchDelivery> = [
  { title: '目标群', key: 'group_id' }, { title: '类型', key: 'kind', render: (row) => row.kind === 'dynamic' ? '动态' : '直播' },
  { title: '状态', key: 'status', render: (row) => h(NTag, { size: 'small', type: row.status === 'sent' ? 'success' : row.status === 'failed' ? 'error' : 'warning' }, { default: () => row.status }) },
  { title: '尝试次数', key: 'attempts' }, { title: '时间', key: 'updated_at', render: (row) => format(row.updated_at) },
  { title: '错误', key: 'error', render: (row) => row.error || '—' },
]
const configuredCookieCount = computed(() => [config.value?.sessdata_configured, config.value?.bili_jct_configured, config.value?.dede_user_id_configured].filter(Boolean).length)
onMounted(load)
</script>

<template>
  <section class="page biliwatch-page">
    <PageHeader title="B站推送" description="按 QQ 群订阅指定 UP 主的动态和直播，后台自动轮询并记录投递结果。">
      <NButton secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</NButton>
      <NButton type="primary" @click="openCreate"><template #icon><Plus :size="16" /></template>添加订阅</NButton>
    </PageHeader>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="biliwatch-grid">
      <NCard title="运行配置" :bordered="true">
        <NForm label-placement="top" size="small">
          <NFormItem label="管理员 QQ"><NInput v-model:value="adminsText" placeholder="多个 QQ 号用逗号分隔" /></NFormItem>
          <NFormItem label="轮询间隔（秒）"><NInputNumber v-model:value="pollInterval" :min="20" :max="3600" style="width: 100%" /></NFormItem>
          <NFormItem label="B 站 Cookie"><NInput v-model:value="sessdata" type="password" placeholder="SESSDATA（留空表示保持原值）" /></NFormItem>
          <NFormItem label="bili_jct"><NInput v-model:value="biliJct" type="password" placeholder="留空表示保持原值" /></NFormItem>
          <NFormItem label="DedeUserID"><NInput v-model:value="dedeUserId" type="password" placeholder="留空表示保持原值" /></NFormItem>
        </NForm>
        <div class="config-status"><NTag size="small" :type="configuredCookieCount ? 'success' : 'warning'">{{ configuredCookieCount }}/3 个 Cookie 字段已配置</NTag><span class="muted">Cookie 只保存在本机，不会回显</span></div>
        <div class="form-actions"><NButton secondary @click="testCookie">测试 Cookie</NButton><NButton type="primary" :loading="saving" @click="saveConfig">保存配置</NButton></div>
      </NCard>
      <NCard title="工作方式" :bordered="true">
        <div class="biliwatch-explain"><BellRing :size="22" /><div><strong>确定性后台推送</strong><p>轮询、去重、重试和发送均由业务服务完成，不调用 LLM。首次添加动态订阅时只记录当前最新内容作为基线。</p></div></div>
        <div class="biliwatch-stats"><div><strong>{{ subscriptions.length }}</strong><span>订阅</span></div><div><strong>{{ deliveries.length }}</strong><span>最近投递</span></div><div><strong>{{ configuredCookieCount }}</strong><span>Cookie 字段</span></div></div>
      </NCard>
    </div>
    <NCard title="订阅管理" :bordered="true">
      <NEmpty v-if="!loading && subscriptions.length === 0" description="暂无订阅，请先添加一个 QQ 群和 UP 主" />
      <NDataTable v-else :loading="loading" :columns="subscriptionColumns" :data="subscriptions" :bordered="false" />
    </NCard>
    <NCard title="最近投递记录" :bordered="true">
      <NEmpty v-if="!loading && deliveries.length === 0" description="暂无投递记录" />
      <NDataTable v-else :loading="loading" :columns="deliveryColumns" :data="deliveries" :bordered="false" />
    </NCard>
    <NModal v-model:show="showForm" preset="card" :title="editing ? '编辑 B站订阅' : '添加 B站订阅'" style="width: min(560px, calc(100vw - 32px))">
      <NForm label-placement="top">
        <NFormItem label="QQ Bot ID"><NInput v-model:value="form.bot_id" placeholder="例如 10000" /></NFormItem>
        <NFormItem label="QQ群号"><NInput v-model:value="form.group_id" placeholder="例如 30001" /></NFormItem>
        <NFormItem label="UP 主 UID"><NInput v-model:value="form.uid" placeholder="例如 123456" /></NFormItem>
        <div class="switch-grid"><NFormItem label="监测动态"><NSwitch v-model:value="form.watch_dynamic" /></NFormItem><NFormItem label="监测直播"><NSwitch v-model:value="form.watch_live" /></NFormItem><NFormItem label="动态 @全体"><NSwitch v-model:value="form.at_all_dynamic" /></NFormItem><NFormItem label="直播 @全体"><NSwitch v-model:value="form.at_all_live" /></NFormItem><NFormItem label="屏蔽转发"><NSwitch v-model:value="form.filter_forward" /></NFormItem><NFormItem label="启用订阅"><NSwitch v-model:value="form.enabled" /></NFormItem></div>
      </NForm>
      <div class="form-actions"><NButton secondary @click="showForm = false">取消</NButton><NButton type="primary" :loading="saving" @click="saveSubscription">保存订阅</NButton></div>
    </NModal>
  </section>
</template>

<style scoped>
.biliwatch-page { display: grid; gap: 18px; }
.biliwatch-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 18px; }
.config-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.form-actions { display: flex; justify-content: flex-end; gap: 9px; margin-top: 18px; }
.biliwatch-explain { display: flex; gap: 13px; color: var(--ng-primary); }
.biliwatch-explain strong { color: var(--ng-text); font-size: 14px; }
.biliwatch-explain p { margin: 7px 0 0; color: var(--ng-muted); font-size: 12px; line-height: 1.7; }
.biliwatch-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 26px; }
.biliwatch-stats div { padding: 12px; background: #f7faf8; border: 1px solid var(--ng-border); border-radius: 7px; }
.biliwatch-stats strong, .biliwatch-stats span { display: block; }
.biliwatch-stats strong { font-size: 20px; }
.biliwatch-stats span { margin-top: 3px; color: var(--ng-muted); font-size: 11px; }
.switch-grid { display: grid; grid-template-columns: repeat(2, 1fr); column-gap: 24px; }
@media (max-width: 800px) { .biliwatch-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) { .switch-grid { grid-template-columns: 1fr; } }
</style>
