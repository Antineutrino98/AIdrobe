/** 衣橱缺口：把后端返回的中文结论直接给人看，不再倒出一堆 JSON 代码。 */
import { useEffect, useRef, useState } from 'react'
import { errorMessage, gapReport } from '../api/client'
import { Button } from '../components/ui'
import { BarChart3 } from 'lucide-react'
import { CardSkeleton } from '../components/Skeleton'
import type { PushToast } from '../components/Toast'
import { zhCategory, zhStatKey } from '../lib/i18n'
import { describeWait, useElapsed } from '../lib/useElapsed'
import type { GapReport, WardrobeScope } from '../types'

interface GapTabProps {
  ownerId: string
  scope: WardrobeScope
  onToast: PushToast
}

function formatValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(2)
  }
  if (typeof value === 'string') return value
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

/** 统计概览：只挑人能看懂的标量，嵌套对象单独处理。 */
function StatOverview({ statistics }: { statistics: Record<string, unknown> }) {
  const highlights = [
    'owned_garment_count',
    'lookback_days',
    'sessions_considered',
  ]
  const byCategory = statistics.owned_by_category
  const sceneStats = statistics.scene_stats

  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-3 gap-2">
        {highlights.map((key) => (
          <div key={key} className="rounded-xl bg-slate-50 px-2 py-2.5 text-center">
            <p className="text-lg font-semibold text-slate-800">
              {formatValue(statistics[key])}
            </p>
            <p className="mt-0.5 text-[10px] leading-4 text-slate-500">
              {zhStatKey(key)}
            </p>
          </div>
        ))}
      </div>

      {byCategory && typeof byCategory === 'object' ? (
        <div>
          <p className="text-xs font-medium text-slate-600">各品类数量</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {Object.entries(byCategory as Record<string, unknown>).map(
              ([key, count]) => (
                <span
                  key={key}
                  className="rounded-lg bg-indigo-50 px-2 py-1 text-[11px] text-indigo-700"
                >
                  {zhCategory(key)} · {formatValue(count)}
                </span>
              ),
            )}
          </div>
        </div>
      ) : null}

      {sceneStats && typeof sceneStats === 'object' ? (
        <div>
          <p className="text-xs font-medium text-slate-600">场景表现</p>
          <div className="mt-1.5 space-y-1.5">
            {Object.entries(sceneStats as Record<string, unknown>).map(
              ([scene, raw]) => {
                const values =
                  raw && typeof raw === 'object'
                    ? (raw as Record<string, unknown>)
                    : {}
                return (
                  <div
                    key={scene}
                    className="rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-600"
                  >
                    <span className="font-medium text-slate-800">
                      {zhCategory(scene)}
                    </span>
                    <span className="ml-2">
                      {Object.entries(values)
                        .filter(([, v]) => typeof v !== 'object')
                        .map(([k, v]) => `${zhStatKey(k)} ${formatValue(v)}`)
                        .join(' · ')}
                    </span>
                  </div>
                )
              },
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function GapTab({ ownerId, scope, onToast }: GapTabProps) {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<GapReport | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const waited = useElapsed(busy)

  // 00:51：切语言后清掉旧语言的分析结论，避免中文界面出现英文。
  useEffect(() => {
    const onLanguageChange = () => {
      if (busy) return
      setReport(null)
      setLastError(null)
    }
    window.addEventListener('aidrobe:language-change', onLanguageChange)
    return () => window.removeEventListener('aidrobe:language-change', onLanguageChange)
  }, [busy])

  // 21:56 反馈：缺口分析也是长任务，允许中途取消。
  const abortRef = useRef<AbortController | null>(null)
  function cancelPending() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    onToast('已取消本次分析', 'info')
  }

  async function analyze() {
    if (!ownerId) {
      onToast('请先在顶部选择用户，或手动填入用户 ID', 'error')
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setLastError(null)
    try {
      setReport(await gapReport(ownerId, scope, controller.signal))
      onToast('分析完成', 'success')
    } catch (error) {
      if (controller.signal.aborted) return
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  const summary = String(report?.summary ?? '').trim()
  const gaps = Array.isArray(report?.gaps) ? (report?.gaps as string[]) : []
  const statistics = (report?.statistics ?? {}) as Record<string, unknown>

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="t-title text-slate-900">衣橱缺口</h2>
        <p className="t-meta mt-1">
          看看还缺哪些场景或品类，避免重复购买。
        </p>
        <Button variant="soft" className="mt-3" disabled={busy} onClick={() => void analyze()}>
          <BarChart3 size={15} />
          {busy ? describeWait(waited) : '分析我的衣橱'}
        </Button>
      </div>

      {lastError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm">
          <p className="font-medium text-rose-800">这次分析失败了</p>
          <p className="mt-1 break-all text-xs leading-5 text-rose-700">
            {lastError}
          </p>
          <p className="mt-1.5 text-xs text-rose-500">
            「设置 → 诊断」里的请求日志有每次请求的状态码和耗时。
          </p>
        </div>
      )}

      {busy && (
        <div className="space-y-3">
          <button
            type="button"
            onClick={cancelPending}
            className="rounded-lg border px-3 py-1.5 transition hover:opacity-75"
            style={{ borderColor: 'var(--line)', color: 'var(--ink-2)', fontSize: 12 }}
          >
            取消本次分析
          </button>
          <CardSkeleton />
        </div>
      )}

      {!busy && report && (
        <>
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="t-subtitle text-slate-900">结论</h3>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              {summary || '暂时没有可总结的缺口，先多穿几次再来看看。'}
            </p>
          </div>

          {gaps.length > 0 && (
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h3 className="t-subtitle text-slate-900">发现的缺口</h3>
              <ul className="mt-2 space-y-2">
                {gaps.map((item, index) => (
                  <li
                    key={`${index}-${item}`}
                    className="flex gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900"
                  >
                    <span className="shrink-0 font-semibold">{index + 1}.</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="t-subtitle text-slate-900">统计概览</h3>
            <StatOverview statistics={statistics} />
          </div>

        </>
      )}

      {!busy && !report && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          点上面的按钮开始分析。
        </div>
      )}
    </section>
  )
}
