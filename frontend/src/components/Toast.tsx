/** 中文 toast：API 失败不弹窗（M6 交互要求）。 */
import { useCallback, useRef, useState } from 'react'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  text: string
  kind: ToastKind
}

export type PushToast = (text: string, kind?: ToastKind) => void

const STYLES: Record<ToastKind, string> = {
  success: 'bg-emerald-600 text-white',
  error: 'bg-rose-600 text-white',
  info: 'bg-slate-800 text-white',
}

/**
 * 停留时长：错误必须留够看的时间。
 * 之前统一 3.2 秒，用户没盯着屏幕就等于「什么都没提示」。
 */
const DURATION_MS: Record<ToastKind, number> = {
  success: 3000,
  info: 4000,
  error: 12000,
}

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id))
  }, [])

  const push = useCallback<PushToast>(
    (text, kind = 'info') => {
      const id = nextId.current++
      setToasts((current) => [...current, { id, text, kind }])
      window.setTimeout(() => dismiss(id), DURATION_MS[kind])
    },
    [dismiss],
  )

  return { toasts, push, dismiss }
}

export function ToastHost({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[]
  onDismiss: (id: number) => void
}) {
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-[100] flex flex-col items-center gap-2 px-4">
      {toasts.map((toast) => (
        <button
          key={toast.id}
          type="button"
          onClick={() => onDismiss(toast.id)}
          className={`pointer-events-auto max-w-lg rounded-xl px-4 py-2.5 text-sm shadow-lg ${STYLES[toast.kind]}`}
        >
          {toast.text}
        </button>
      ))}
    </div>
  )
}
