/**
 * 桌面窗口（WebView2）里输入框没有原生右键菜单，用户只能 Ctrl+V。
 * 这里做一个轻量的自定义菜单，覆盖全部 input / textarea。
 * 用原生 value setter + input 事件，保证 React 受控组件能收到 onChange。
 */
import { useEffect, useState } from 'react'

type Editable = HTMLInputElement | HTMLTextAreaElement

interface MenuState {
  x: number
  y: number
  el: Editable
}

export function TextContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [hint, setHint] = useState('')

  useEffect(() => {
    function onContext(event: MouseEvent) {
      const target = event.target as HTMLElement | null
      if (!target || (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA')) return
      event.preventDefault()
      setHint('')
      setMenu({ x: event.clientX, y: event.clientY, el: target as Editable })
    }
    function dismiss() {
      setMenu(null)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') dismiss()
    }
    document.addEventListener('contextmenu', onContext)
    document.addEventListener('click', dismiss)
    document.addEventListener('keydown', onKey)
    window.addEventListener('blur', dismiss)
    window.addEventListener('resize', dismiss)
    return () => {
      document.removeEventListener('contextmenu', onContext)
      document.removeEventListener('click', dismiss)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('resize', dismiss)
    }
  }, [])

  if (!menu) return null
  const el = menu.el

  function setNativeValue(value: string) {
    const proto =
      el instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
    if (setter) setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }

  function replaceSelection(text: string) {
    const start = el.selectionStart ?? el.value.length
    const end = el.selectionEnd ?? el.value.length
    setNativeValue(el.value.slice(0, start) + text + el.value.slice(end))
    const caret = start + text.length
    try {
      el.setSelectionRange(caret, caret)
    } catch {
      // 部分 type 不支持选区，忽略
    }
    el.focus()
  }

  function selectedText(): string {
    return el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0)
  }

  async function paste(): Promise<boolean> {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        replaceSelection(text)
        return true
      }
    } catch {
      // WebView2 偶尔不给 clipboard-read 权限，退回键盘。
    }
    setHint('读不到剪贴板，请按 Ctrl+V')
    el.focus()
    return false
  }

  async function copy(cut = false): Promise<boolean> {
    const text = selectedText()
    if (!text) return true
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      setHint('复制失败，请按 Ctrl+C')
      return false
    }
    if (cut) replaceSelection('')
    return true
  }

  // 返回 false 表示失败了：菜单必须留着，否则提示一闪而过，看起来像没反应。
  const actions: { label: string; run: () => boolean | void | Promise<boolean | void> }[] = [
    { label: '粘贴', run: paste },
    { label: '复制', run: () => copy(false) },
    { label: '剪切', run: () => copy(true) },
    { label: '全选', run: () => { el.select(); el.focus() } },
  ]

  return (
    <div
      className="fixed z-[200] min-w-[132px] rounded-xl border p-1 shadow-lg"
      style={{
        left: Math.max(0, Math.min(menu.x, window.innerWidth - 150)),
        top: Math.max(0, Math.min(menu.y, window.innerHeight - 190)),
        background: 'var(--surface)',
        borderColor: 'var(--line)',
      }}
      onClick={(event) => event.stopPropagation()}
    >
      {actions.map((action) => (
        <button
          key={action.label}
          type="button"
          className="block w-full rounded-lg px-3 py-1.5 text-left transition hover:bg-slate-100"
          style={{ fontSize: 12.5, color: 'var(--ink)' }}
          onClick={async () => {
            const ok = await action.run()
            if (ok !== false) setMenu(null)
          }}
        >
          {action.label}
        </button>
      ))}
      {hint && <p className="px-3 py-1.5 t-meta" style={{ color: 'var(--danger)' }}>{hint}</p>}
    </div>
  )
}
