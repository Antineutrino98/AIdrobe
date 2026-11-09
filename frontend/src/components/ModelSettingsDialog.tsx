/** 模型设置：三个角色各自选模型（带价格）+ 各服务商 Key + 真实连通性自检。 */
import { useEffect, useState } from 'react'
import {
  errorMessage,
  getSettings,
  pingHealth,
  saveSettings,
  testModel,
  type SettingsPayload,
} from '../api/client'
import {
  MODEL_PRESETS,
  PROVIDERS,
  PROVIDER_LABELS,
  ROLE_KEYS,
  ROLE_LABELS,
  findModel,
  priceLabel,
  type Provider,
  type RoleKey,
} from '../lib/models'
import { getRoleModelId, setRoleModelId } from '../lib/storage'
import type { PushToast } from './Toast'

interface Props {
  open: boolean
  onClose: () => void
  onChanged: () => void
  onToast: PushToast
}

type LineState = 'idle' | 'pending' | 'ok' | 'fail'

interface TestLine {
  key: string
  label: string
  state: LineState
  note: string
}

function zhCallError(raw: string | null | undefined): string {
  const text = String(raw ?? '')
  if (/403|denied|permission/i.test(text)) return '密钥没有该模型的权限'
  if (/401|unauthorized|invalid api key/i.test(text)) return '密钥无效'
  if (/429|rate limit|quota/i.test(text)) return '调用过于频繁或额度用尽'
  if (/timeout|timed out/i.test(text)) return '响应超时'
  if (/connection|network/i.test(text)) return '网络连接失败'
  if (!text) return '调用失败'
  return '调用失败'
}

const INITIAL_LINES: TestLine[] = [
  { key: 'network', label: '后端连接', state: 'idle', note: '未测试' },
  ...ROLE_KEYS.map((role) => ({
    key: role,
    label: ROLE_LABELS[role],
    state: 'idle' as LineState,
    note: '未测试',
  })),
]

export function ModelSettingsDialog({ open, onClose, onChanged, onToast }: Props) {
  const [roles, setRoles] = useState<Record<RoleKey, string>>({
    vision: 'qwen37',
    planner: 'qwen38',
    stylist: 'qwen38',
  })
  const emptyKeys = (): Record<Provider, string> =>
    Object.fromEntries(PROVIDERS.map((p) => [p, ''])) as Record<Provider, string>
  const [keys, setKeys] = useState<Record<Provider, string>>(emptyKeys)
  const [lines, setLines] = useState<TestLine[]>(INITIAL_LINES)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    if (!open) return
    const savedRoles: Record<RoleKey, string> = {
      vision: getRoleModelId('vision'),
      planner: getRoleModelId('planner'),
      stylist: getRoleModelId('stylist'),
    }
    setRoles(savedRoles)
    setLines(INITIAL_LINES)
    // U4：Key 只存在后端 .env，界面拿到的始终是脱敏值（带 *）。
    void (async () => {
      try {
        const info = await getSettings()
        const byProvider: Record<string, string> = {}
        for (const role of ROLE_KEYS) {
          const provider = findModel(savedRoles[role]).provider
          const masked = info.roles[role]?.api_key ?? ''
          if (masked && !byProvider[provider]) byProvider[provider] = masked
        }
        setKeys((current) => ({ ...current, ...byProvider }))
      } catch {
        setKeys(emptyKeys())
      }
    })()
  }, [open])

  if (!open) return null

  // 只检查当前三个角色真正用到的服务商，没选的模型不要求填 Key。
  const usedProviders = Array.from(
    new Set(ROLE_KEYS.map((role) => findModel(roles[role]).provider)),
  )
  const missingProviders = usedProviders.filter((provider) => !keys[provider]?.trim())

  /** U4 裁决 3：写入后端 .env 并热重载，不再写 localStorage。 */
  async function persist(): Promise<void> {
    for (const role of ROLE_KEYS) setRoleModelId(role, roles[role])
    const payload: SettingsPayload = { roles: {} }
    for (const role of ROLE_KEYS) {
      const preset = findModel(roles[role])
      const key = keys[preset.provider] ?? ''
      payload.roles[role] = {
        base_url: preset.baseUrl,
        model: preset.model,
        // 脱敏值带 *，后端会跳过、保留原 Key；空串表示明确清除。
        api_key: key.trim(),
      }
    }
    await saveSettings(payload)
    onChanged()
  }

  async function save() {
    try {
      await persist()
    } catch (error) {
      onToast(errorMessage(error), 'error')
      return
    }
    if (missingProviders.length > 0) {
      const names = missingProviders.map((item) => PROVIDER_LABELS[item]).join('、')
      onToast(`还没填${names}的密钥，相关功能会调用失败`, 'error')
    } else {
      onToast('模型设置已保存，立即生效', 'success')
    }
    onClose()
  }

  async function clearAll() {
    const payload: SettingsPayload = {
      roles: Object.fromEntries(
        ROLE_KEYS.map((role) => [role, { api_key: '' }]),
      ),
    }
    try {
      await saveSettings(payload)
      setKeys(emptyKeys())
      onChanged()
      onToast('已清除后端保存的 Key', 'info')
    } catch (error) {
      onToast(errorMessage(error), 'error')
    }
  }

  function patchLine(key: string, patch: Partial<TestLine>) {
    setLines((current) =>
      current.map((line) => (line.key === key ? { ...line, ...patch } : line)),
    )
  }

  async function runTest() {
    try {
      await persist()
    } catch (error) {
      onToast(errorMessage(error), 'error')
      return
    }
    setTesting(true)
    setLines(
      INITIAL_LINES.map((line) =>
        line.key === 'network'
          ? { ...line, state: 'pending', note: '测试中…' }
          : { ...line, state: 'idle', note: '等待中…' },
      ),
    )

    // 1) 后端连通
    try {
      const ping = await pingHealth()
      patchLine('network', {
        state: 'ok',
        note: `正常，${ping.ms} 毫秒`,
      })
    } catch (error) {
      patchLine('network', { state: 'fail', note: errorMessage(error) })
      setTesting(false)
      return
    }

    // 2) 三个角色逐个测（串行，避免把连接打满）
    for (const role of ROLE_KEYS) {
      patchLine(role, { state: 'pending', note: '调用中…' })
      try {
        const result = await testModel(role)
        const preset = MODEL_PRESETS.find((item) => item.model === result.model)
        const label = preset?.label ?? '当前模型'
        if (result.ok) {
          const visionHint =
            result.vision_ok === false ? ' · 读图异常' : ''
          patchLine(role, {
            state: result.vision_ok === false ? 'fail' : 'ok',
            note: `${label} · ${(result.ms / 1000).toFixed(1)} 秒${visionHint}`,
          })
        } else {
          patchLine(role, {
            state: 'fail',
            note: `${label} · ${zhCallError(result.error)}`,
          })
        }
      } catch (error) {
        patchLine(role, { state: 'fail', note: errorMessage(error) })
      }
    }
    setTesting(false)
  }

  const stateMark: Record<LineState, string> = {
    idle: '○',
    pending: '…',
    ok: '✓',
    fail: '✗',
  }
  const stateColor: Record<LineState, string> = {
    idle: 'text-slate-400',
    pending: 'text-indigo-500',
    ok: 'text-emerald-600',
    fail: 'text-rose-600',
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 px-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="t-title">模型设置</h2>
        <p className="t-meta mt-1">
          三个环节分别选模型，价格是每百万 token 的输入 / 输出。Key 保存在本机 .env，界面只显示前后 4 位。
        </p>

        <div className="mt-4 space-y-3">
          {ROLE_KEYS.map((role) => {
            const selected = findModel(roles[role])
            return (
              <div key={role} className="rounded-xl border border-slate-200 p-3">
                <label
                  className="block font-medium text-slate-700"
                  style={{ fontSize: 13 }}
                  htmlFor={`role-${role}`}
                >
                  {ROLE_LABELS[role]}
                </label>
                <select
                  id={`role-${role}`}
                  value={roles[role]}
                  onChange={(event) =>
                    setRoles((current) => ({
                      ...current,
                      [role]: event.target.value,
                    }))
                  }
                  className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 outline-none focus:border-indigo-400"
                  style={{ fontSize: 13 }}
                >
                  {MODEL_PRESETS.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label} · 输入 {item.priceInput} / 输出 {item.priceOutput}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-slate-500" style={{ fontSize: 11 }}>
                  {priceLabel(selected)} · {selected.note}
                </p>
              </div>
            )
          })}
        </div>

        <div className="mt-4 space-y-3">
          {PROVIDERS.map((provider) => (
            <div key={provider}>
              <label
                className="block font-medium text-slate-600"
                style={{ fontSize: 12 }}
                htmlFor={`key-${provider}`}
              >
                {PROVIDER_LABELS[provider]} 密钥
                <span className="ml-1.5 font-normal text-slate-400">
                  （选用该服务商的模型时才需要）
                </span>
              </label>
              <input
                id={`key-${provider}`}
                type="password"
                value={keys[provider] ?? ''}
                onChange={(event) =>
                  setKeys((current) => ({ ...current, [provider]: event.target.value }))
                }
                placeholder="粘贴密钥；已保存的只显示前后 4 位"
                autoComplete="off"
                className="ui-field mt-1.5 w-full"
                style={{ fontSize: 13 }}
              />
            </div>
          ))}
        </div>

        {/* ------------------------------------------------ 自检 */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium text-slate-700" style={{ fontSize: 13 }}>
              连通性自检
            </p>
            <button
              type="button"
              disabled={testing}
              onClick={() => void runTest()}
              className="rounded-lg bg-slate-800 px-3 py-1.5 font-medium text-white transition hover:bg-slate-900 disabled:opacity-50"
              style={{ fontSize: 12 }}
            >
              {testing ? '测试中…' : '测试连接'}
            </button>
          </div>
          <ul className="mt-2 space-y-1">
            {lines.map((line) => (
              <li key={line.key} className="flex items-start gap-2" style={{ fontSize: 12 }}>
                <span className={`${stateColor[line.state]} shrink-0`}>
                  {stateMark[line.state]}
                </span>
                <span className="w-24 shrink-0 text-slate-600">{line.label}</span>
                <span className={`min-w-0 flex-1 break-words ${stateColor[line.state]}`}>
                  {line.note}
                </span>
              </li>
            ))}
          </ul>
          <p className="t-caption mt-2 leading-4">
            入库识别与搭配决策会真的传一张图；标记为「读图异常」就说明该模型读不了图，
            换回 Qwen 即可。
          </p>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void save()}
            className="flex-1 rounded-lg bg-indigo-600 px-4 py-2.5 font-medium text-white hover:bg-indigo-700"
            style={{ fontSize: 13 }}
          >
            保存
          </button>
          <button
            type="button"
            onClick={() => void clearAll()}
            className="rounded-lg border border-slate-200 px-4 py-2.5 text-slate-600 hover:bg-slate-50"
            style={{ fontSize: 13 }}
          >
            清除全部 Key
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2.5 text-slate-600 hover:bg-slate-50"
            style={{ fontSize: 13 }}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
