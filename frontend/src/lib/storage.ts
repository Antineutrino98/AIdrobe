/** localStorage：当前用户、衣橱范围、每个角色的模型、两套模型 Key。 */
import type { WardrobeScope } from '../types'
import { ROLE_DEFAULTS, type Provider, type RoleKey } from './models'

const USER_KEY = 'aidrobe.user_id'
const SCOPE_KEY = 'aidrobe.wardrobe_scope'
const MODEL_KEY = 'aidrobe.model_id'
const ROLE_KEY_PREFIX = 'aidrobe.role.'
const LEGACY_API_KEY = 'aidrobe.api_key'
const KEY_PREFIX = 'aidrobe.key.'

const VALID_SCOPES: WardrobeScope[] = ['mine', 'shared', 'all']

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // 隐私模式下 localStorage 可能不可写，忽略即可
  }
}

function remove(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    // 同上
  }
}

export function getStoredUserId(): string | null {
  return read(USER_KEY)
}

export function setStoredUserId(userId: string): void {
  write(USER_KEY, userId)
}

export function getStoredScope(): WardrobeScope {
  const value = read(SCOPE_KEY) as WardrobeScope | null
  return value && VALID_SCOPES.includes(value) ? value : 'mine'
}

export function setStoredScope(scope: WardrobeScope): void {
  write(SCOPE_KEY, scope)
}

// ------------------------------------------------------------ 逐角色模型选择
export function getRoleModelId(role: RoleKey): string {
  // 兼容早期「单一模型」设置：planner 角色继承它。
  const legacy = role === 'planner' ? read(MODEL_KEY) : null
  return read(ROLE_KEY_PREFIX + role) ?? legacy ?? ROLE_DEFAULTS[role]
}

export function setRoleModelId(role: RoleKey, modelId: string): void {
  write(ROLE_KEY_PREFIX + role, modelId)
}

/** 兼容旧调用点：返回文本规划角色的模型。 */
export function getSelectedModelId(): string {
  return getRoleModelId('planner')
}

export function setSelectedModelId(modelId: string): void {
  for (const role of ['vision', 'planner', 'stylist'] as RoleKey[]) {
    setRoleModelId(role, modelId)
  }
  write(MODEL_KEY, modelId)
}

// ------------------------------------------------------------------ 两套 Key
function migrateLegacyKey(): void {
  const legacy = read(LEGACY_API_KEY)
  if (legacy && !read(KEY_PREFIX + 'qwen')) {
    write(KEY_PREFIX + 'qwen', legacy)
  }
}

export function getProviderKey(provider: Provider): string | null {
  migrateLegacyKey()
  return read(KEY_PREFIX + provider)
}

export function setProviderKey(provider: Provider, apiKey: string): void {
  write(KEY_PREFIX + provider, apiKey.trim())
}

export function clearProviderKey(provider: Provider): void {
  remove(KEY_PREFIX + provider)
  if (provider === 'qwen') remove(LEGACY_API_KEY)
}

export function getStoredApiKey(): string | null {
  return getProviderKey('qwen')
}

export function setStoredApiKey(apiKey: string): void {
  setProviderKey('qwen', apiKey)
}

export function clearStoredApiKey(): void {
  clearProviderKey('qwen')
}

// ---------------------------------------------------------------- U4 欢迎向导
const WIZARD_SKIPPED_KEY = 'aidrobe.wizard_skipped'

/** 用户点过「稍后配置，先看看」就不再强制弹全屏向导。 */
export function hasSkippedWizard(): boolean {
  return read(WIZARD_SKIPPED_KEY) === '1'
}

export function skipWizard(): void {
  write(WIZARD_SKIPPED_KEY, '1')
}
