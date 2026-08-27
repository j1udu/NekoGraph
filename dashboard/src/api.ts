import type {
  ActiveModel,
  ChatResponse,
  DashboardConfig,
  HistoryMessage,
  LogEntry,
  ModelProfile,
  ModelProfileInput,
  ModelProfileUpdate,
  RuntimeStatus,
  ToolInfo,
  ScheduledTask,
  ScheduledTaskRequest,
  TaskRun,
  ConnectedOneBot,
  OneBotActionRecord,
  KnowledgeCollection,
  KnowledgeDocument,
  KnowledgeSearchResult,
  BiliWatchConfig,
  BiliWatchConfigUpdate,
  BiliWatchSubscription,
  BiliWatchSubscriptionRequest,
  BiliWatchDelivery,
} from './types'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(payload?.detail ?? `Request failed with HTTP ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return await response.json() as T
}

export const api = {
  status: () => request<RuntimeStatus>('/api/status'),
  tools: () => request<ToolInfo[]>('/api/tools'),
  config: () => request<DashboardConfig>('/api/config'),
  logs: (limit = 100) => request<LogEntry[]>(`/api/logs?limit=${limit}`),
  onebotBots: () => request<ConnectedOneBot[]>('/api/onebot/bots'),
  onebotActions: (limit = 100) => request<OneBotActionRecord[]>(`/api/onebot/actions?limit=${limit}`),
  history: (conversationId: string) =>
    request<HistoryMessage[]>(`/api/chat/${conversationId}/messages`),
  send: (conversationId: string, text: string) =>
    request<ChatResponse>(`/api/chat/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  reset: (conversationId: string) =>
    request<ChatResponse>(`/api/chat/${conversationId}/reset`, { method: 'POST' }),
  models: () => request<ModelProfile[]>('/api/models'),
  importModels: (profiles: ModelProfileInput[]) =>
    request<ModelProfile[]>('/api/models/import', {
      method: 'POST',
      body: JSON.stringify({ profiles }),
    }),
  activateModel: (profileId: string) =>
    request<ActiveModel>(`/api/models/${profileId}/activate`, { method: 'POST' }),
  activateEnvironment: () =>
    request<ActiveModel>('/api/models/environment/activate', { method: 'POST' }),
  updateModel: (profileId: string, profile: ModelProfileUpdate) =>
    request<ModelProfile>(`/api/models/${profileId}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),
  deleteModel: (profileId: string) =>
    request<void>(`/api/models/${profileId}`, { method: 'DELETE' }),
  testModel: (profileId: string) =>
    request<{ ok: boolean; message: string }>(`/api/models/${profileId}/test`, { method: 'POST' }),
  deleteConversation: (conversationId: string) =>
    request<void>(`/api/chat/${conversationId}`, { method: 'DELETE' }),
  scheduledTaskHandlers: () => request<string[]>('/api/scheduled-task-handlers'),
  scheduledTasks: () => request<ScheduledTask[]>('/api/scheduled-tasks'),
  createScheduledTask: (task: ScheduledTaskRequest) => request<ScheduledTask>('/api/scheduled-tasks', {
    method: 'POST', body: JSON.stringify(task),
  }),
  updateScheduledTask: (taskId: string, task: ScheduledTaskRequest) => request<ScheduledTask>(`/api/scheduled-tasks/${taskId}`, {
    method: 'PUT', body: JSON.stringify(task),
  }),
  deleteScheduledTask: (taskId: string) => request<void>(`/api/scheduled-tasks/${taskId}`, { method: 'DELETE' }),
  runScheduledTask: (taskId: string) => request<{ status: string }>(`/api/scheduled-tasks/${taskId}/run`, { method: 'POST' }),
  scheduledTaskRuns: (taskId: string) => request<TaskRun[]>(`/api/scheduled-tasks/${taskId}/runs`),
  knowledgeBases: () => request<KnowledgeCollection[]>('/api/knowledge-bases'),
  createKnowledgeBase: (name: string, description = '') => request<KnowledgeCollection>('/api/knowledge-bases', {
    method: 'POST', body: JSON.stringify({ name, description }),
  }),
  knowledgeDocuments: (collection: string) => request<KnowledgeDocument[]>(`/api/knowledge-bases/${collection}/documents`),
  uploadKnowledgeDocument: async (collection: string, file: File, title?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    const response = await fetch(`/api/knowledge-bases/${collection}/documents/upload`, { method: 'POST', body: form })
    if (!response.ok) throw new Error(`Request failed with HTTP ${response.status}`)
    return await response.json() as KnowledgeDocument
  },
  importKnowledgeUrl: (collection: string, url: string) => request<KnowledgeDocument>(`/api/knowledge-bases/${collection}/documents/url`, {
    method: 'POST', body: JSON.stringify({ url }),
  }),
  deleteKnowledgeDocument: (collection: string, documentId: string) => request<void>(`/api/knowledge-bases/${collection}/documents/${documentId}`, { method: 'DELETE' }),
  rebuildKnowledge: (collection: string) => request<{ status: string }>(`/api/knowledge-bases/${collection}/rebuild`, { method: 'POST' }),
  searchKnowledge: (collection: string, query: string, limit = 5) => request<{ found: boolean, results: KnowledgeSearchResult[] }>(`/api/knowledge-bases/${collection}/search`, {
    method: 'POST', body: JSON.stringify({ query, limit }),
  }),
  knowledgeModels: () => request<import('./types').KnowledgeModelConfig>('/api/knowledge/models'),
  testKnowledgeModel: (payload: { kind: 'embedding' | 'reranker', base_url: string, model: string, api_key: string }) => request<{ ok: boolean, kind: string, dimension?: number, score_count?: number }>('/api/knowledge/models/test', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  importKnowledgeModel: (payload: { kind: 'embedding' | 'reranker', base_url: string, model: string, api_key: string, timeout_seconds?: number }) => request<{ configured: boolean, base_url: string, model: string }>(`/api/knowledge/models`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  deleteKnowledgeModel: (kind: 'embedding' | 'reranker') => request<void>(`/api/knowledge/models/${kind}`, { method: 'DELETE' }),
  biliWatchConfig: () => request<BiliWatchConfig>('/api/biliwatch/config'),
  updateBiliWatchConfig: (config: BiliWatchConfigUpdate) => request<BiliWatchConfig>('/api/biliwatch/config', {
    method: 'PUT', body: JSON.stringify(config),
  }),
  testBiliWatchCookie: () => request<{ ok: boolean }>('/api/biliwatch/cookie/test', { method: 'POST' }),
  biliWatchSubscriptions: () => request<BiliWatchSubscription[]>('/api/biliwatch/subscriptions'),
  createBiliWatchSubscription: (subscription: BiliWatchSubscriptionRequest) => request<BiliWatchSubscription>('/api/biliwatch/subscriptions', {
    method: 'POST', body: JSON.stringify(subscription),
  }),
  updateBiliWatchSubscription: (id: string, subscription: BiliWatchSubscriptionRequest) => request<BiliWatchSubscription>(`/api/biliwatch/subscriptions/${id}`, {
    method: 'PUT', body: JSON.stringify(subscription),
  }),
  deleteBiliWatchSubscription: (id: string) => request<void>(`/api/biliwatch/subscriptions/${id}`, { method: 'DELETE' }),
  biliWatchDeliveries: (limit = 100) => request<BiliWatchDelivery[]>(`/api/biliwatch/deliveries?limit=${limit}`),
}
