import { useEffect, useState } from 'react'
import { clearStoredApiKey, getStoredApiKey, setStoredApiKey } from '../lib/storage'
import type { PushToast } from './Toast'

interface ApiKeyDialogProps {
  open: boolean
  onClose: () => void
  onChanged: () => void
  onToast: PushToast
}

/**
 * 模型 API Key 输入框。
 *
 * - 只写 localStorage（键名 `aidrobe.api_key`），不写后端数据库；
 * - 每次 API 请求由 `api/client.ts` 读取并放到 `X-API-Key`；
 * - 后端只在当前请求的内存里使用，不落盘、不写日志。
 */
export function ApiKeyDialog({ open, onClose, onChanged, onToast }: ApiKeyDialogProps) {
  const [value, setValue] = useState('')

  useEffect(() => {
    if (open) setValue(getStoredApiKey() ?? '')
  }, [open])

  if (!open) return null

  function save() {
    const trimmed = value.trim()
    if (!trimmed) {
      onToast('请先填写 API Key', 'error')
      return
    }
    setStoredApiKey(trimmed)
    onChanged()
    onToast('密钥已保存到本机浏览器', 'success')
    onClose()
  }

  function clear() {
    clearStoredApiKey()
    setValue('')
    onChanged()
    onToast('密钥已清除', 'info')
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-slate-900">模型 API Key</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          只保存在本机浏览器 localStorage，随请求发送给你自己的后端；
          不要在公共电脑使用。
        </p>
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="sk-..."
          autoComplete="off"
          className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400"
        />
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={save}
            className="flex-1 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white"
          >
            保存
          </button>
          <button
            type="button"
            onClick={clear}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600"
          >
            清除
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
