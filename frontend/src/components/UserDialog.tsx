/** 用户管理：新建、改名、删除。删除前会先查出会连带删掉多少件衣物。 */
import { useEffect, useState } from 'react'
import {
  createUser,
  deleteUser,
  errorMessage,
  getUserSummary,
  renameUser,
} from '../api/client'
import type { PushToast } from './Toast'
import type { User } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  users: User[]
  currentUserId: string
  onUsersChanged: () => void
  onUserDeleted: (id: string) => void
  onToast: PushToast
}

export function UserDialog({
  open,
  onClose,
  users,
  currentUserId,
  onUsersChanged,
  onUserDeleted,
  onToast,
}: Props) {
  const [newName, setNewName] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<{
    id: string
    name: string
    garmentCount: number
  } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) {
      setEditingId(null)
      setPendingDelete(null)
      setNewName('')
    }
  }, [open])

  if (!open) return null

  async function add() {
    const name = newName.trim()
    if (!name) {
      onToast('请先填写用户名', 'error')
      return
    }
    setBusy(true)
    try {
      await createUser(name)
      setNewName('')
      onUsersChanged()
      onToast(`已添加「${name}」`, 'success')
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  async function saveRename(id: string) {
    const name = editingName.trim()
    if (!name) {
      onToast('用户名不能为空', 'error')
      return
    }
    setBusy(true)
    try {
      await renameUser(id, name)
      setEditingId(null)
      onUsersChanged()
      onToast(`已改名为「${name}」`, 'success')
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  async function askDelete(user: User) {
    setBusy(true)
    try {
      const summary = await getUserSummary(user.id)
      setPendingDelete({
        id: user.id,
        name: summary.display_name,
        garmentCount: summary.garment_count,
      })
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    if (users.length <= 1) {
      onToast('至少要保留一个用户', 'error')
      setPendingDelete(null)
      return
    }
    setBusy(true)
    try {
      const result = await deleteUser(pendingDelete.id)
      onUserDeleted(pendingDelete.id)
      setPendingDelete(null)
      onUsersChanged()
      onToast(
        `已删除「${result.display_name}」及其 ${result.deleted_garments} 件衣物与全部记录`,
        'success',
      )
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 px-4"
      onClick={onClose}
    >
      <div
        className="max-h-[88vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-slate-900">用户管理</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          家庭成员各建一个用户名，衣橱数据互相独立。没有登录，切换即生效。
        </p>

        <div className="mt-4 flex gap-2">
          <input
            value={newName}
            disabled={busy}
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void add()
            }}
            placeholder="新用户名，例如：妈妈"
            className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void add()}
            className="shrink-0 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            添加
          </button>
        </div>

        <ul className="mt-4 divide-y divide-slate-100">
          {users.length === 0 && (
            <li className="py-4 text-center text-sm text-slate-400">
              还没有用户，先在上面添加一个。
            </li>
          )}
          {users.map((user) => (
            <li key={user.id} className="py-2.5">
              <div className="flex items-center gap-2">
                {editingId === user.id ? (
                  <input
                    value={editingName}
                    autoFocus
                    onChange={(event) => setEditingName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void saveRename(user.id)
                      if (event.key === 'Escape') setEditingId(null)
                    }}
                    className="min-w-0 flex-1 rounded-lg border border-indigo-300 px-2 py-1.5 text-sm outline-none"
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">
                    {user.display_name}
                    {user.id === currentUserId && (
                      <span className="ml-2 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-normal text-emerald-700">
                        当前
                      </span>
                    )}
                  </span>
                )}
                {editingId === user.id ? (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void saveRename(user.id)}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingId(null)}
                      className="text-xs text-slate-500 hover:underline"
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(user.id)
                        setEditingName(user.display_name)
                        setPendingDelete(null)
                      }}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      改名
                    </button>
                    <button
                      type="button"
                      onClick={() => void askDelete(user)}
                      className="text-xs text-rose-600 hover:underline"
                    >
                      删除
                    </button>
                  </>
                )}
              </div>

              {pendingDelete?.id === user.id && (
                <div className="mt-2 rounded-xl bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-800">
                  <p className="font-medium">
                    确定删除「{pendingDelete.name}」？
                  </p>
                  <p className="mt-1">
                    会同时删除其名下 <b>{pendingDelete.garmentCount}</b> 件衣物、
                    推荐记录、反馈与偏好历史。此操作不可撤销。
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void confirmDelete()}
                      className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
                    >
                      确认删除
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(null)}
                      className="rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-700"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
