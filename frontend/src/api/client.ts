/**
 * M6 前端 API 客户端：只发 HTTP，不接触数据库。
 *
 * 接口现状（2026-11-06 M7 完成后核查）：
 *   已实现（M2/M3/M4/M5/M7）：POST /ingest/image、/ingest/url、
 *                       /ingest/confirm-category、POST /recommend、
 *                       GET /images/{id}.jpg、POST /evaluate-purchase、
 *                       POST /gap-report、POST /feedback、
 *                       GET /preference、GET /preference/history、
 *                       POST /preference/clear、GET /users、
 *                       GET /garments/{id}
 *   全部接口已实现。
 *
 * 对未实现的接口，这里仍然真的发请求；后端返回 404/405 时统一转换成
 * 中文 ApiError「接口尚未实现」。
 */
import type {
  FeedbackPayload,
  GapReport,
  GarmentCreateResult,
  PurchaseEvaluation,
  RecommendPayload,
  RecommendPolicy,
  RecommendResponse,
  User,
  WardrobeItem,
  WardrobeScope,
} from '../types'
import { ROLE_KEYS, findModel, type RoleKey } from '../lib/models'
import { getRoleModelId } from '../lib/storage'
import { getLanguage } from '../lib/language'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ------------------------------------------------------- 请求日志（排障用）
/**
 * 记录最近 20 次请求：方法、路径、状态码、耗时、错误原文。
 * 目的是让「点了没反应」可解释——用户能直接看到是超时、404 还是 500。
 * 只存在内存，不落盘、不上报。
 */
export interface RequestLogEntry {
  id: number
  method: string
  path: string
  status: number | null
  ms: number
  error?: string
  at: string
}

const MAX_LOGS = 20
let logs: RequestLogEntry[] = []
let nextLogId = 1
const logListeners = new Set<(entries: RequestLogEntry[]) => void>()

function record(entry: Omit<RequestLogEntry, 'id' | 'at'>): void {
  const item: RequestLogEntry = {
    ...entry,
    id: nextLogId++,
    at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
  }
  logs = [item, ...logs].slice(0, MAX_LOGS)
  for (const listener of logListeners) listener(logs)
}

export function getRequestLogs(): RequestLogEntry[] {
  return logs
}

export function subscribeRequestLog(
  listener: (entries: RequestLogEntry[]) => void,
): () => void {
  logListeners.add(listener)
  return () => {
    logListeners.delete(listener)
  }
}

async function parseError(
  response: Response,
): Promise<{ message: string; code?: string }> {
  try {
    const body = (await response.json()) as {
      detail?: unknown
      message?: unknown
      code?: unknown
    }
    const code = typeof body.code === 'string' ? body.code : undefined
    let message = ''
    if (typeof body.message === 'string') message = body.message
    else if (typeof body.detail === 'string') message = body.detail
    else if (body.detail != null) message = JSON.stringify(body.detail)
    // 后端按 U4 模块三返回的是已翻译好的友好提示，绝不渲染原始堆栈。
    if (message) return { message, code }
  } catch {
    // 响应不是 JSON，用状态文本兜底
  }
  return { message: `请求没成功（状态 ${response.status}）` }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const startedAt = performance.now()
  const elapsed = (): number => Math.round(performance.now() - startedAt)

  const headers = new Headers(init?.headers)
  // U4 裁决 3：Key / 模型统一存后端 .env，前端不再发任何 X-*-API-Key 头。
  // 21:56：告诉后端当前界面语言，让模型生成的理由 / 结论同语言，
  // 英文界面不再出现「这双厚底小白鞋…8 outfits」这种中英夹杂。
  headers.set('X-UI-Language', getLanguage())

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  } catch {
    record({ method, path, status: null, ms: elapsed(), error: '网络不可达' })
    throw new ApiError('无法连接服务器，请检查网络或后端是否已启动')
  }
  if (!response.ok) {
    const parsed = await parseError(response)
    // 后端缺接口时给出人话，而不是裸露的 404。
    const finalMessage =
      response.status === 404 || response.status === 405
        ? '这个功能的后端接口还没做好'
        : parsed.message
    record({
      method,
      path,
      status: response.status,
      ms: elapsed(),
      error: finalMessage,
    })
    throw new ApiError(finalMessage, response.status, parsed.code)
  }
  if (response.status === 204) {
    record({ method, path, status: response.status, ms: elapsed() })
    return undefined as T
  }
  try {
    const data = (await response.json()) as T
    record({ method, path, status: response.status, ms: elapsed() })
    return data
  } catch {
    // 200 但不是 JSON：常见于代理返回 HTML 错误页，必须显式报出来，
    // 否则解析异常会在业务层被误当成别的错误。
    record({
      method,
      path,
      status: response.status,
      ms: elapsed(),
      error: '响应不是合法 JSON',
    })
    throw new ApiError('服务端返回的内容不是 JSON，可能是代理或后端异常')
  }
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

/** 后端 /images/{id}.jpg 的 URL；开发时经 vite 代理转发。 */
export function imageUrl(pathOrId: string): string {
  if (/^https?:\/\//.test(pathOrId)) return pathOrId
  const path = pathOrId.startsWith('/') ? pathOrId : `/images/${pathOrId}.jpg`
  return `${API_BASE}${path}`
}

// ---------------------------------------------------------------- 用户
/**
 * GET /users（M4/M5 范围）。兼容后端返回 User[] 或 { users: User[] } 两种形状。
 */
export async function getUsers(): Promise<User[]> {
  const data = await request<User[] | { users?: User[] }>('/users')
  const list = Array.isArray(data) ? data : (data.users ?? [])
  return list.map((item) => ({
    id: String(item.id),
    display_name: String(item.display_name ?? item.id),
    created_at: item.created_at,
    settings: item.settings,
  }))
}

// ---------------------------------------------------------------- 用户管理
export async function createUser(displayName: string): Promise<User> {
  return request<User>(
    '/users',
    jsonInit('POST', { display_name: displayName }),
  )
}

export async function renameUser(
  userId: string,
  displayName: string,
): Promise<User> {
  return request<User>(
    `/users/${encodeURIComponent(userId)}`,
    jsonInit('PATCH', { display_name: displayName }),
  )
}

export async function getUserSummary(
  userId: string,
): Promise<{ id: string; display_name: string; garment_count: number }> {
  return request(`/users/${encodeURIComponent(userId)}/summary`)
}

export async function deleteUser(
  userId: string,
): Promise<{ deleted_garments: number; display_name: string }> {
  return request(`/users/${encodeURIComponent(userId)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------- 我的衣橱
/** 列出已入库衣物；不传 ownerId 表示「全部衣橱」。 */
export async function listGarments(
  ownerId?: string,
  scope: WardrobeScope = 'mine',
): Promise<WardrobeItem[]> {
  const params = new URLSearchParams({ scope })
  if (ownerId) params.set('owner_id', ownerId)
  return request<WardrobeItem[]>(`/garments?${params.toString()}`)
}

export async function deleteGarment(
  garmentId: string,
): Promise<{ deleted_garment_id: string }> {
  return request(`/garments/${encodeURIComponent(garmentId)}`, {
    method: 'DELETE',
  })
}

/** U2：衣橱卡片上标记待洗 / 洗干净了。 */
export async function setWearState(
  garmentId: string,
  wearState: 'clean' | 'worn_once' | 'dirty' | 'laundry',
): Promise<{ ok: boolean }> {
  return request(
    `/garments/${encodeURIComponent(garmentId)}/wear-state`,
    jsonInit('PATCH', { wear_state: wearState }),
  )
}

// ---------------------------------------------------------------- 自检
export interface ModelTestResult {
  role: string
  ok: boolean
  /** 仅视觉角色有值：接口 200 但读不出图里的颜色时为 false。 */
  vision_ok?: boolean | null
  model: string | null
  ms: number
  stage: string
  sample?: string
  error: string | null
}

/** 后端是否活着（不碰模型、不碰数据库）。 */
export async function pingHealth(): Promise<{ ok: boolean; ms: number }> {
  const started = performance.now()
  await request<{ ok: boolean }>('/health')
  return { ok: true, ms: Math.round(performance.now() - started) }
}

/** 实测某个角色能不能调通；视觉角色会真的传一张图。 */
export async function testModel(role: string): Promise<ModelTestResult> {
  return request<ModelTestResult>(
    '/test-model',
    jsonInit('POST', { role }),
  )
}

// ---------------------------------------------------------------- 推荐策略
export async function getPolicy(userId: string): Promise<RecommendPolicy> {
  return request<RecommendPolicy>(
    `/users/${encodeURIComponent(userId)}/policy`,
  )
}

export async function putPolicy(
  userId: string,
  policy: RecommendPolicy,
): Promise<RecommendPolicy> {
  return request<RecommendPolicy>(
    `/users/${encodeURIComponent(userId)}/policy`,
    jsonInit('PUT', policy),
  )
}

// ---------------------------------------------------------------- 入库（M2 已实现）
export async function ingestImage(
  file: File,
  ownerId: string,
  /** 'purchase' = 购前候选，只建 wishlist 记录，不进衣橱与推荐。 */
  purpose?: 'purchase',
): Promise<GarmentCreateResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('owner_id', ownerId)
  if (purpose) form.append('purpose', purpose)
  return request<GarmentCreateResult>('/ingest/image', {
    method: 'POST',
    body: form,
  })
}

export async function ingestUrl(
  url: string,
  ownerId: string,
  purpose?: 'purchase',
): Promise<GarmentCreateResult> {
  return request<GarmentCreateResult>(
    '/ingest/url',
    jsonInit('POST', { url, owner_id: ownerId, purpose }),
  )
}

export async function confirmCategory(
  garmentId: string,
  category: string,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    '/ingest/confirm-category',
    jsonInit('POST', { garment_id: garmentId, category }),
  )
}

// ---------------------------------------------------------------- 推荐（M3 已实现）
export async function recommend(
  payload: RecommendPayload,
  signal?: AbortSignal,
): Promise<RecommendResponse> {
  return request<RecommendResponse>(
    '/recommend',
    { ...jsonInit('POST', payload), signal },
  )
}

// ---------------------------------------------------------------- 反馈（M4/M5 范围）
export async function sendFeedback(
  payload: FeedbackPayload,
): Promise<unknown> {
  return request<unknown>('/feedback', jsonInit('POST', payload))
}

// ---------------------------------------------------------------- 购前评估（M4/M5 范围）
/**
 * M6 没有给 /evaluate-purchase 的请求形状。这里按「先 /ingest 拿到
 * garment_id，再拿这个 id 去评估」的两步流程调用；字段保持最小。
 */
export async function evaluatePurchase(
  ownerId: string,
  garmentId: string,
  signal?: AbortSignal,
): Promise<PurchaseEvaluation> {
  return request<PurchaseEvaluation>(
    '/evaluate-purchase',
    { ...jsonInit('POST', { owner_id: ownerId, garment_id: garmentId }), signal },
  )
}

// ---------------------------------------------------------------- 缺口分析（M4/M5 范围，可选 Tab）
export async function gapReport(
  ownerId: string,
  scope: WardrobeScope,
  signal?: AbortSignal,
): Promise<GapReport> {
  return request<GapReport>(
    '/gap-report',
    { ...jsonInit('POST', { owner_id: ownerId, wardrobe_scope: scope }), signal },
  )
}

/** 给 UI 用的中文错误文本。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '发生未知错误，请稍后重试'
}


// ------------------------------------------------------- U4：设置与用量
export interface SettingsInfo {
  roles: Record<
    RoleKey,
    { base_url: string; model: string; api_key: string; has_key: boolean }
  >
  has_garments: boolean
  has_valid_keys: boolean
}

export interface UsageInfo {
  sessions: number
  outfit_count: number
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface SettingsPayload {
  roles: Record<string, { base_url?: string; model?: string; api_key?: string }>
  common_api_key?: string
}

export async function getSettings(): Promise<SettingsInfo> {
  return request<SettingsInfo>('/settings')
}

export async function saveSettings(
  payload: SettingsPayload,
): Promise<{ ok: boolean; changed: string[]; has_valid_keys: boolean }> {
  return request('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getUsage(): Promise<UsageInfo> {
  return request<UsageInfo>('/usage')
}

/**
 * U4 裁决 5：桌面端 WebView 的 localStorage 是全新存储区，
 * 把浏览器时代留下的旧 Key 静默搬到后端 .env，然后清空旧数据。
 * 返回是否真的迁移过；失败时静默忽略，下次启动会再试。
 */
export async function migrateLegacyKeys(): Promise<boolean> {
  const keys: Record<string, string> = {}
  for (const provider of ['qwen', 'deepseek']) {
    const value = localStorage.getItem(`aidrobe.key.${provider}`)
    if (value?.trim()) keys[provider] = value.trim()
  }
  const legacy = localStorage.getItem('aidrobe.api_key')
  if (legacy?.trim() && !keys.qwen) keys.qwen = legacy.trim()
  if (Object.keys(keys).length === 0) return false

  const roles: SettingsPayload['roles'] = {}
  for (const role of ROLE_KEYS) {
    const preset = findModel(getRoleModelId(role))
    const apiKey = keys[preset.provider] ?? ''
    roles[role] = {
      base_url: preset.baseUrl,
      model: preset.model,
      api_key: apiKey,
    }
  }
  try {
    await saveSettings({ roles })
  } catch {
    return false
  }
  for (const provider of ['qwen', 'deepseek']) {
    localStorage.removeItem(`aidrobe.key.${provider}`)
  }
  localStorage.removeItem('aidrobe.api_key')
  return true
}
