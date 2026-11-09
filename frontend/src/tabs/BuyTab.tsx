/** M6 Tab 3「买不买」：上传 wishlist 图片/链接 → 购前评估结果卡片。 */
import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ShoppingBag, Sparkles } from 'lucide-react'
import {
  errorMessage,
  evaluatePurchase,
  imageUrl,
  ingestImage,
  ingestUrl,
} from '../api/client'
import type { PushToast } from '../components/Toast'
import { Button, Input, Skeleton } from '../components/ui'
import { compressImage } from '../lib/image'
import { zhCategory, zhVerdict } from '../lib/i18n'
import { describeWait, useElapsed } from '../lib/useElapsed'
import type { PurchaseEvaluation } from '../types'

interface BuyTabProps {
  ownerId: string
  onToast: PushToast
}

function isHighRisk(value: unknown): boolean {
  const text = String(value ?? '').toLowerCase()
  return text === 'high' || text === '高' || text === 'true'
}

/** 结论徽章配色：绿=建议买，红=别买，黄=再想想。 */
function verdictTone(verdict: string): string {
  const key = verdict.toLowerCase()
  if (key === 'buy' || key.includes('买') && !key.includes('别') && !key.includes('再')) {
    return '#6d8a70'
  }
  if (key === 'skip' || key.includes('跳过') || key.includes('别买')) {
    return '#a9645f'
  }
  return '#b08a4a'
}

export function BuyTab({ ownerId, onToast }: BuyTabProps) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<PurchaseEvaluation | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const waited = useElapsed(busy)

  // 00:51：切语言后清掉旧语言的评估结论，避免中文界面出现英文。
  useEffect(() => {
    const onLanguageChange = () => {
      if (busy) return
      setResult(null)
      setLastError(null)
    }
    window.addEventListener('aidrobe:language-change', onLanguageChange)
    return () => window.removeEventListener('aidrobe:language-change', onLanguageChange)
  }, [busy])

  // 21:56 反馈：评估同样可能跑一两分钟，允许中途取消。
  const abortRef = useRef<AbortController | null>(null)
  function cancelPending() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    onToast('已取消本次评估', 'info')
  }

  function requireOwner(): boolean {
    if (ownerId) return true
    onToast('请先在顶部选择用户，或手动填入用户 ID', 'error')
    return false
  }

  /**
   * M4 已实现 /evaluate-purchase，后端同时兼容 owner_id/garment_id 与
   * user_id/candidate_garment_id 两种写法。这里仍是
   * 「先 /ingest 拿 garment_id，再拿 id 评估」两步走。
   */
  async function evaluate(garmentId: string, signal?: AbortSignal) {
    const evaluation = await evaluatePurchase(ownerId, garmentId, signal)
    setResult(evaluation)
    onToast('评估完成', 'success')
  }

  async function handleFile(file: File | undefined) {
    if (!file || !requireOwner()) return
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setResult(null)
    setLastError(null)
    try {
      const compressed = await compressImage(file)
      const ingest = await ingestImage(compressed, ownerId, 'purchase')
      await evaluate(ingest.garment_id, controller.signal)
    } catch (error) {
      if (controller.signal.aborted) return
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function handleUrl() {
    const value = url.trim()
    if (!value) {
      onToast('请先粘贴商品链接', 'error')
      return
    }
    if (!requireOwner()) return
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setResult(null)
    setLastError(null)
    try {
      const ingest = await ingestUrl(value, ownerId, 'purchase')
      await evaluate(ingest.garment_id, controller.signal)
    } catch (error) {
      if (controller.signal.aborted) return
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  const verdict = result?.verdict ?? result?.llm_verdict ?? ''
  const reasoning = result?.reasoning ?? result?.llm_reasoning ?? ''
  const similar = result?.similar_items ?? []

  return (
    <section className="space-y-4">
      <div className="card-soft p-4">
        <p className="t-meta mb-3">
          这里上传的图片只用于这次评估，不会收进衣橱，也不会出现在穿搭推荐里。
        </p>
        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          onChange={(event) => void handleFile(event.target.files?.[0])}
        />
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          <ShoppingBag size={17} />
          {busy ? describeWait(waited) : '上传想买的图片'}
        </Button>

        <div className="my-4 flex items-center gap-3 t-meta">
          <span className="h-px flex-1" style={{ background: 'var(--line)' }} />
          或者
          <span className="h-px flex-1" style={{ background: 'var(--line)' }} />
        </div>

        <div className="flex gap-2">
          <Input
            type="url"
            value={url}
            disabled={busy}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="粘贴商品链接"
            className="min-w-0 flex-1"
            aria-label="商品链接"
          />
          <Button variant="outline" disabled={busy} onClick={() => void handleUrl()}>
            评估
          </Button>
        </div>
      </div>

      {lastError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm">
          <p className="font-medium text-rose-800">这次评估失败了</p>
          <p className="mt-1 break-all text-xs leading-5 text-rose-700">
            {lastError}
          </p>
          <p className="mt-1.5 text-xs text-rose-500">
            「设置 → 诊断」里的请求日志有每次请求的状态码和耗时。
          </p>
        </div>
      )}

      {/* U2：评估中的骨架屏 + 明确提示语 */}
      {busy && (
        <div className="card-soft p-5">
          <div className="flex items-center gap-2" style={{ color: 'var(--sage-deep)' }}>
            <Sparkles size={17} />
            <span className="font-medium" style={{ fontSize: 14 }}>
              AI 正在结合你的衣橱进行深度评估…
            </span>
          </div>
          <p className="t-meta mt-1">{describeWait(waited)}</p>
          <button
            type="button"
            onClick={cancelPending}
            className="mt-3 rounded-lg border px-3 py-1.5 transition hover:opacity-75"
            style={{ borderColor: 'var(--line)', color: 'var(--ink-2)', fontSize: 12 }}
          >
            取消本次评估
          </button>
          <div className="mt-4 space-y-3">
            <Skeleton className="h-24 w-full" />
            <div className="grid grid-cols-2 gap-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        </div>
      )}

      {!busy && result && (
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="card-soft p-5"
        >
          {/* 巨大的绿 / 红 / 黄结论徽章 */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="t-meta">评估结论</span>
            <span
              className="rounded-2xl px-6 py-3 font-bold text-white"
              style={{
                fontSize: 24,
                background: verdictTone(verdict),
                boxShadow: '0 8px 20px rgba(45,42,38,0.14)',
              }}
            >
              {zhVerdict(verdict)}
            </span>
          </div>

          <p className="t-body mt-4" style={{ color: 'var(--ink-2)' }}>
            {reasoning || '（这次评估没有给出理由）'}
          </p>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <div
              className="rounded-xl p-3 text-center"
              style={{ background: 'var(--surface-2)' }}
            >
              <p className="t-num font-semibold" style={{ fontSize: 26 }}>
                {result.feasible_outfit_count ?? '—'}
              </p>
              <p className="t-meta mt-0.5">可搭配套数</p>
            </div>
            <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
              <p className="t-meta">可覆盖场景</p>
              <p className="t-body-sm mt-1">
                {(result.scene_coverage ?? []).map(zhCategory).join('、') || '—'}
              </p>
            </div>
          </div>

          {isHighRisk(result.duplicate_risk) && similar.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-medium text-amber-700">
                重复风险较高 · 衣橱里已有类似单品
              </p>
              <div className="no-scrollbar mt-2 flex gap-2 overflow-x-auto pb-1">
                {similar.map((item) => (
                  <div key={item.garment_id} className="shrink-0">
                    <div className="h-20 w-16 overflow-hidden rounded-lg bg-slate-100">
                      <img
                        src={imageUrl(item.image_url ?? item.garment_id)}
                        alt={zhCategory(item.category ?? '相似衣物')}
                        className="h-full w-full object-cover"
                        onError={(event) => {
                          event.currentTarget.style.visibility = 'hidden'
                        }}
                      />
                    </div>
                    <p className="mt-1 w-16 truncate text-center text-[11px] text-slate-500">
                      {zhCategory(item.subcategory ?? item.category) || '单品'}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </motion.div>
      )}
    </section>
  )
}
