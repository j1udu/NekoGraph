export interface ActiveModel {
  profile_id: string | null
  name: string
  model: string
  base_url: string | null
  source: string
}

export interface RuntimeStatus {
  version: string
  started_at: string
  uptime_seconds: number
  model: ActiveModel
  model_profile_count: number
  tool_count: number
  checkpoint: string
  gateway: string
}

export interface ModelProfile {
  profile_id: string
  name: string
  model: string
  base_url: string
  temperature: number
  timeout_seconds: number
  created_at: string
  active: boolean
  has_api_key: boolean
}

export interface ModelProfileInput {
  name: string
  model: string
  base_url: string
  api_key: string
  temperature: number
  timeout_seconds: number
}

export interface ModelProfileUpdate extends Omit<ModelProfileInput, 'api_key'> {
  api_key?: string
}

export interface ToolInfo {
  name: string
  description: string
  source: string
  risk: 'safe' | 'sensitive' | 'dangerous'
  timeout_seconds: number
  required_permissions: string[]
}

export interface HistoryMessage {
  role: string
  content: string
  tool_calls: Record<string, unknown>[]
}

export interface ChatResponse {
  message_id: string
  content: string
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
}

export interface LogEntry {
  timestamp: string
  level: string
  logger: string
  event: string
  [key: string]: unknown
}

export interface DashboardConfig {
  onebot: {
    host: string
    port: number
    path: string
    access_token_configured: boolean
  }
  dashboard: {
    host: string
    port: number
  }
  agent: {
    checkpoint_backend: string
    group_conversation_mode: string
    group_wake_prefixes: string[]
  }
  tools: {
    permissions: string[]
    allow_dangerous: boolean
    approval_ttl_seconds: number
  }
}
