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
}
