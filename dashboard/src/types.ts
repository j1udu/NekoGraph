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
  connected_bot_count: number
  scheduled_task_count: number
}

export interface ConnectedOneBot {
  bot_id: string
  connected_at: string
}

export interface OneBotActionRecord {
  action_id: string
  bot_id: string
  action: string
  risk: 'safe' | 'sensitive' | 'dangerous'
  source: string
  correlation_id: string | null
  target_summary: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'
  retcode: number | null
  error: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
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
  response_time_ms: number | null
}

export interface ChatResponse {
  message_id: string
  content: string
  response_time_ms: number | null
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
    action_timeout_seconds: number
    action_max_concurrency: number
    send_min_interval_seconds: number
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

export type ScheduleKind = 'cron' | 'interval' | 'once'
export type TaskStatus = 'scheduled' | 'running' | 'completed' | 'failed' | 'disabled' | 'unavailable'

export interface ScheduledTask {
  task_id: string
  name: string
  handler_name: string
  schedule_kind: ScheduleKind
  cron_expression: string | null
  interval_seconds: number | null
  run_at: string | null
  timezone: string
  payload: Record<string, unknown>
  enabled: boolean
  status: TaskStatus
  last_run_at: string | null
  next_run_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface ScheduledTaskRequest {
  name: string
  handler_name: string
  schedule_kind: ScheduleKind
  cron_expression?: string | null
  interval_seconds?: number | null
  run_at?: string | null
  timezone: string
  payload: Record<string, unknown>
  enabled: boolean
}

export interface TaskRun {
  run_id: string
  task_id: string
  scheduled_at: string
  started_at: string
  finished_at: string | null
  status: TaskStatus
  error: string | null
  duration_ms: number | null
}
