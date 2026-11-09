import { useEffect, useState } from 'react'
import {
  getRequestLogs,
  subscribeRequestLog,
  type RequestLogEntry,
} from '../api/client'

/**
 * 请求日志：设置抽屉里点开后**就地展开**，不是绝对定位浮层。
 * 旧版浮层在抽屉的 overflow 容器里被裁掉，只剩一列状态字。
 */
const METHOD_ZH: Record<string, string> = {
  GET: '读取',
  POST: '提交',
  PATCH: '修改',
  PUT: '保存',
  DELETE: '删除',
}

const PATH_ZH: Record<string, string> = {
  '/users': '用户',
  '/garments': '衣物',
  '/recommend': '推荐搭配',
  '/ingest/image': '图片入库',
  '/ingest/url': '链接入库',
  '/ingest/confirm-category': '确认品类',
  '/evaluate-purchase': '购前评估',
  '/gap-report': '缺口分析',
  '/feedback': '搭配反馈',
  '/preference': '偏好',
  '/preference/history': '偏好历史',
  '/preference/clear': '清除偏好',
  '/test-model': '模型自检',
  '/health': '后端状态',
  '/users/policy': '推荐策略',
}

function zhAction(method: string, path: string): string {
  const clean = path.split('?')[0].replace(/^\/api/, '')
  const name = PATH_ZH[clean] ?? '接口'
  return `${METHOD_ZH[method] ?? '请求'}${name}`
}

function zhStatus(status: number | null): string {
  if (status === null) return '无响应'
  if (status < 300) return '成功'
  if (status === 401) return '密钥无效'
  if (status === 403) return '无权限'
  if (status === 404) return '未找到'
  if (status === 429) return '额度或频率受限'
  if (status >= 500) return '服务失败'
  return '请求被拒'
}

export function RequestLogPanel() {
  const [open, setOpen] = useState(false)
  const [entries, setEntries] = useState<RequestLogEntry[]>(() => getRequestLogs())

  useEffect(() => subscribeRequestLog(setEntries), [])

  const errorCount = entries.filter((item) => item.error).length

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 font-medium transition ${
          errorCount > 0
            ? 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100'
            : 'border-slate-200 text-slate-600 hover:bg-slate-50'
        }`}
        style={{ fontSize: 12.5 }}
      >
        <span>请求日志（{entries.length} 条{errorCount > 0 ? `，${errorCount} 条失败` : ''}）</span>
        <span>{open ? '收起' : '展开'}</span>
      </button>

      {open && (
        <div
          className="mt-2 max-h-[360px] w-full overflow-y-auto rounded-xl border p-3"
          style={{ borderColor: 'var(--line)', background: 'var(--surface)' }}
        >
          {entries.length === 0 ? (
            <p className="py-4 text-center" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
              还没有请求记录。
            </p>
          ) : (
            <ul className="divide-y" style={{ borderColor: 'var(--line)' }}>
              {entries.map((item) => (
                <li key={item.id} className="py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className="min-w-0 break-all font-medium"
                      style={{ fontSize: 12.5, color: 'var(--ink)' }}
                    >
                      {zhAction(item.method, item.path)}
                    </span>
                    <span
                      className={`t-num shrink-0 ${item.error ? 'text-rose-600' : 'text-emerald-600'}`}
                      style={{ fontSize: 12 }}
                    >
                      {zhStatus(item.status)} · {item.status ?? '—'}
                    </span>
                  </div>
                  <p className="t-caption t-num mt-0.5">
                    {item.at} · {item.ms} 毫秒 · {item.method} {item.path}
                  </p>
                  {item.error && (
                    <p
                      className="mt-1 break-all rounded-md bg-rose-50 px-2 py-1 text-rose-700"
                      style={{ fontSize: 11, lineHeight: 1.5 }}
                    >
                      {item.error}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
