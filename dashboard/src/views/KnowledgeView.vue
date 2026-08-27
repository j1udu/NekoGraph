<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NButton, NCard, NEmpty, NForm, NFormItem, NInput, NInputGroup, NList, NListItem, NSelect, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
import PageHeader from '../components/layout/PageHeader.vue'
import { api } from '../api'
import type { KnowledgeCollection, KnowledgeDocument, KnowledgeModelConfig, KnowledgeSearchResult } from '../types'

const message = useMessage()
const collections = ref<KnowledgeCollection[]>([])
const documents = ref<KnowledgeDocument[]>([])
const selected = ref('yousa')
const query = ref('')
const url = ref('')
const results = ref<KnowledgeSearchResult[]>([])
const loading = ref(false)
const uploading = ref(false)
const modelConfig = ref<KnowledgeModelConfig | null>(null)
const modelTesting = ref<'embedding' | 'reranker' | null>(null)
const modelError = ref('')
const modelForms = ref({
  embedding: { base_url: '', model: '', api_key: '' },
  reranker: { base_url: '', model: '', api_key: '' },
})
const fileInput = ref<HTMLInputElement | null>(null)
const collectionOptions = computed(() => collections.value.map((item) => ({ label: item.name, value: item.name })))
const documentCountLabel = computed(() => `${documents.value.length} 个文档`)
const embeddingConfigured = computed(() => Boolean(modelConfig.value?.embedding.configured))
const rerankerConfigured = computed(() => Boolean(modelConfig.value?.reranker.configured))

async function loadCollections() {
  collections.value = await api.knowledgeBases()
  if (!collections.value.some((item) => item.name === selected.value) && collections.value[0]) selected.value = collections.value[0].name
  await loadDocuments()
  modelConfig.value = await api.knowledgeModels()
  if (modelConfig.value.embedding.base_url) modelForms.value.embedding.base_url = modelConfig.value.embedding.base_url
  if (modelConfig.value.embedding.model) modelForms.value.embedding.model = modelConfig.value.embedding.model
  if (modelConfig.value.reranker.base_url) modelForms.value.reranker.base_url = modelConfig.value.reranker.base_url
  if (modelConfig.value.reranker.model) modelForms.value.reranker.model = modelConfig.value.reranker.model
}
async function loadDocuments() {
  results.value = []
  documents.value = await api.knowledgeDocuments(selected.value)
}
async function upload() {
  const file = fileInput.value?.files?.[0]
  if (!file) return
  uploading.value = true
  try { await api.uploadKnowledgeDocument(selected.value, file); message.success('文档已导入'); await loadDocuments() } catch (error) { message.error(error instanceof Error ? error.message : '导入失败') } finally { uploading.value = false }
}
async function importUrl() {
  if (!url.value.trim()) return
  uploading.value = true
  try { await api.importKnowledgeUrl(selected.value, url.value.trim()); url.value = ''; message.success('网页已导入'); await loadDocuments() } catch (error) { message.error(error instanceof Error ? error.message : '导入失败') } finally { uploading.value = false }
}
async function search() {
  if (!query.value.trim()) return
  loading.value = true
  try { results.value = (await api.searchKnowledge(selected.value, query.value)).results } catch (error) { message.error(error instanceof Error ? error.message : '检索失败') } finally { loading.value = false }
}
async function remove(document: KnowledgeDocument) {
  await api.deleteKnowledgeDocument(selected.value, document.document_id)
  results.value = []
  message.success('文档已删除')
  await loadDocuments()
}
async function rebuild() { await api.rebuildKnowledge(selected.value); message.success('索引已重建') }
async function testModel(kind: 'embedding' | 'reranker') {
  const form = modelForms.value[kind]
  if (!form.base_url || !form.model || !form.api_key) { modelError.value = '请填写 Base URL、模型 ID 和 API Key'; return }
  modelTesting.value = kind
  modelError.value = ''
  try {
    await api.testKnowledgeModel({ kind, ...form })
    message.success(`${kind === 'embedding' ? '向量模型' : '重排序模型'}连接成功`)
  } catch (error) { modelError.value = error instanceof Error ? error.message : '模型连接测试失败' } finally { modelTesting.value = null }
}
async function saveModel(kind: 'embedding' | 'reranker') {
  const form = modelForms.value[kind]
  if (!form.base_url || !form.model || !form.api_key) { modelError.value = '请填写 Base URL、模型 ID 和 API Key'; return }
  try {
    await api.importKnowledgeModel({ kind, ...form })
    message.success(`${kind === 'embedding' ? '向量模型' : '重排序模型'}已保存`)
    modelConfig.value = await api.knowledgeModels()
  } catch (error) { modelError.value = error instanceof Error ? error.message : '模型保存失败' }
}
onMounted(loadCollections)
</script>

<template>
  <div class="knowledge-page">
    <PageHeader title="专题知识库" description="管理泠鸢 yousa 的背景资料、作品说明和活动记录。">
      <div class="knowledge-toolbar">
        <span class="knowledge-toolbar-label">当前集合</span>
        <NSelect v-model:value="selected" :options="collectionOptions" size="small" class="knowledge-collection-select" @update:value="loadDocuments" />
        <NButton size="small" :loading="uploading" @click="fileInput?.click()">导入文件</NButton>
        <input ref="fileInput" hidden type="file" accept=".md,.markdown,.txt,text/plain,text/markdown" @change="upload" />
        <NButton size="small" secondary @click="rebuild">重建索引</NButton>
      </div>
    </PageHeader>

    <section class="knowledge-workspace">
      <NCard class="knowledge-card" :bordered="true">
        <template #header><div class="knowledge-card-heading"><div><h2>导入网页</h2><p>把公开资料加入当前知识集合</p></div><NTag size="small" type="info">URL</NTag></div></template>
        <NInputGroup><NInput v-model:value="url" placeholder="输入网页地址，例如 https://..." @keyup.enter="importUrl" /><NButton :loading="uploading" type="primary" @click="importUrl">导入</NButton></NInputGroup>
        <p class="knowledge-hint">网页只在导入时抓取一次，不会在每次查询时联网。</p>
      </NCard>

      <NCard class="knowledge-card" :bordered="true">
        <template #header><div class="knowledge-card-heading"><div><h2>测试检索</h2><p>确认当前集合能否召回相关片段</p></div><NTag size="small" type="success">{{ results.length ? `${results.length} 条结果` : '检索' }}</NTag></div></template>
        <NInputGroup><NInput v-model:value="query" placeholder="例如：泠鸢的音乐风格" @keyup.enter="search" /><NButton :loading="loading" type="primary" @click="search">检索</NButton></NInputGroup>
        <div class="knowledge-results">
          <NSpin v-if="loading" size="small" />
          <NEmpty v-else-if="query && results.length === 0" description="没有找到相关资料" />
          <NList v-else-if="results.length" size="small">
            <NListItem v-for="result in results" :key="result.chunk_id"><div class="knowledge-result"><NSpace size="small"><NTag size="small">{{ result.title }}</NTag><span class="muted">{{ result.heading_path }}</span></NSpace><p>{{ result.content }}</p></div></NListItem>
          </NList>
          <span v-else class="knowledge-placeholder">输入问题后查看召回的正文片段</span>
        </div>
      </NCard>
    </section>

    <NCard class="knowledge-models" :bordered="true">
      <template #header><div class="knowledge-section-heading"><div><h2>检索模型接口</h2><p>使用 OpenAI-compatible 接口增强向量检索和结果排序，未配置时仍可使用稀疏检索。</p></div><NTag size="small" :type="embeddingConfigured || rerankerConfigured ? 'success' : 'default'">{{ embeddingConfigured || rerankerConfigured ? '部分已配置' : '仅使用稀疏检索' }}</NTag></div></template>
      <div v-if="modelError" class="error-banner">{{ modelError }}</div>
      <div class="knowledge-model-grid">
        <div class="knowledge-model-card">
          <div class="knowledge-model-heading"><div><h3>向量模型</h3><span>用于语义召回</span></div><NTag size="small" :type="embeddingConfigured ? 'success' : 'warning'">{{ embeddingConfigured ? '已配置' : '未配置' }}</NTag></div>
          <NForm label-placement="top" size="small">
            <NFormItem label="Base URL"><NInput v-model:value="modelForms.embedding.base_url" placeholder="https://provider.example/v1" /></NFormItem>
            <NFormItem label="模型 ID"><NInput v-model:value="modelForms.embedding.model" placeholder="text-embedding-3-small" /></NFormItem>
            <NFormItem label="API Key"><NInput v-model:value="modelForms.embedding.api_key" type="password" show-password-on="click" placeholder="仅保存在本机" /></NFormItem>
          </NForm>
          <div class="knowledge-model-actions"><NButton size="small" :loading="modelTesting === 'embedding'" type="primary" @click="testModel('embedding')">测试连接</NButton><NButton size="small" secondary @click="saveModel('embedding')">保存配置</NButton></div>
        </div>
        <div class="knowledge-model-card">
          <div class="knowledge-model-heading"><div><h3>重排序模型</h3><span>用于优化候选结果顺序</span></div><NTag size="small" :type="rerankerConfigured ? 'success' : 'warning'">{{ rerankerConfigured ? '已配置' : '未配置' }}</NTag></div>
          <NForm label-placement="top" size="small">
            <NFormItem label="Base URL"><NInput v-model:value="modelForms.reranker.base_url" placeholder="https://provider.example/v1" /></NFormItem>
            <NFormItem label="模型 ID"><NInput v-model:value="modelForms.reranker.model" placeholder="bge-reranker-v2-m3" /></NFormItem>
            <NFormItem label="API Key"><NInput v-model:value="modelForms.reranker.api_key" type="password" show-password-on="click" placeholder="仅保存在本机" /></NFormItem>
          </NForm>
          <div class="knowledge-model-actions"><NButton size="small" :loading="modelTesting === 'reranker'" type="primary" @click="testModel('reranker')">测试连接</NButton><NButton size="small" secondary @click="saveModel('reranker')">保存配置</NButton></div>
        </div>
      </div>
    </NCard>

    <NCard class="knowledge-documents" :bordered="true">
      <template #header><div class="knowledge-section-heading"><div><h2>文档</h2><p>当前集合中的资料及其分块状态</p></div><NTag size="small">{{ documentCountLabel }}</NTag></div></template>
      <NEmpty v-if="documents.length === 0" description="暂无文档，请先导入 Markdown、TXT 或网页" />
      <NList v-else>
        <NListItem v-for="document in documents" :key="document.document_id"><div class="knowledge-document"><div><strong>{{ document.title }}</strong><div class="muted">{{ document.source }} · {{ document.chunk_count }} 个片段</div></div><NButton size="small" quaternary type="error" @click="remove(document)">删除</NButton></div></NListItem>
      </NList>
    </NCard>
  </div>
</template>

<style scoped>
.knowledge-page { display: grid; gap: 18px; }
.knowledge-page :deep(.page-header) { margin-bottom: 2px; }
.knowledge-toolbar { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.knowledge-toolbar-label { color: var(--ng-muted); font-size: 12px; }
.knowledge-collection-select { width: 150px; }
.knowledge-workspace { display: grid; grid-template-columns: minmax(0, .92fr) minmax(0, 1.08fr); gap: 18px; align-items: stretch; }
.knowledge-card, .knowledge-models, .knowledge-documents { border-radius: var(--ng-radius); }
.knowledge-card :deep(.n-card__content) { min-height: 142px; }
.knowledge-card-heading, .knowledge-section-heading, .knowledge-model-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.knowledge-card-heading h2, .knowledge-section-heading h2 { margin: 0; font-size: 16px; line-height: 1.3; }
.knowledge-card-heading p, .knowledge-section-heading p { margin: 5px 0 0; color: var(--ng-muted); font-size: 12px; font-weight: 400; line-height: 1.5; }
.knowledge-hint { margin: 13px 0 0; color: var(--ng-muted); font-size: 12px; }
.knowledge-results { min-height: 72px; margin-top: 14px; }
.knowledge-results :deep(.n-empty) { padding: 8px 0; }
.knowledge-placeholder { display: block; padding: 12px 0; color: #89948f; font-size: 12px; }
.knowledge-result p { margin: 8px 0 0; color: #4b5651; font-size: 12px; line-height: 1.65; }
.knowledge-model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.knowledge-model-card { padding: 17px; border: 1px solid var(--ng-border); border-radius: 7px; background: #fbfcfb; }
.knowledge-model-heading { margin-bottom: 12px; align-items: center; }
.knowledge-model-heading h3 { margin: 0; font-size: 14px; }
.knowledge-model-heading span { display: block; margin-top: 3px; color: var(--ng-muted); font-size: 11px; }
.knowledge-model-card :deep(.n-form-item) { margin-bottom: 12px; }
.knowledge-model-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 2px; }
.knowledge-documents :deep(.n-card__content) { padding-top: 4px; }
.knowledge-document { display: flex; align-items: center; justify-content: space-between; gap: 16px; width: 100%; }
.knowledge-document strong { font-size: 13px; }
@media (max-width: 820px) {
  .knowledge-workspace, .knowledge-model-grid { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .knowledge-toolbar { justify-content: flex-start; }
  .knowledge-toolbar-label { width: 100%; }
  .knowledge-collection-select { width: 100%; }
  .knowledge-toolbar .n-button { flex: 1; }
  .knowledge-card :deep(.n-card__content) { min-height: 0; }
}
</style>
