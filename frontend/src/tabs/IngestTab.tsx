/** 入库 + 我的衣橱：一次选多张、并发识别，并立刻看到已入库的衣物。 */
import { useRef, useState } from 'react'
import { UploadCloud } from 'lucide-react'
import {
  confirmCategory,
  errorMessage,
  imageUrl,
  ingestImage,
  ingestUrl,
} from '../api/client'
import { CardSkeleton } from '../components/Skeleton'
import type { PushToast } from '../components/Toast'
import { compressImage, readImageSize } from '../lib/image'
import { zhCategory, zhColors } from '../lib/i18n'
import { describeWait, useElapsed } from '../lib/useElapsed'
import type { GarmentCreateResult } from '../types'

interface IngestTabProps {
  ownerId: string
  onToast: PushToast
  /** 入库成功后通知右栏衣橱刷新。 */
  onWardrobeChanged: () => void
  /** 嵌在「添加衣物」弹窗里时隐藏自带标题，避免和弹窗标题重复。 */
  compact?: boolean
}

/** 同时最多几张图在识别；模型调用可以并发，数据库写入由后端排队。 */
const CONCURRENCY = 3

function asText(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/** 简单的并发池：返回与输入同序的结果，单个失败不影响其他。 */
async function runPool<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<{ ok: boolean; value?: R; error?: string }[]> {
  const results: { ok: boolean; value?: R; error?: string }[] = new Array(
    items.length,
  )
  let cursor = 0
  async function runner(): Promise<void> {
    while (cursor < items.length) {
      const index = cursor++
      try {
        results[index] = { ok: true, value: await worker(items[index], index) }
      } catch (error) {
        results[index] = { ok: false, error: errorMessage(error) }
      }
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, () => runner()),
  )
  return results
}

function ResultCard({
  result,
  onToast,
  onChanged,
}: {
  result: GarmentCreateResult
  onToast: PushToast
  onChanged: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [done, setDone] = useState(false)
  const extracted = result.extracted ?? {}
  const options = result.confirmation_options ?? []
  const needsConfirmation = result.status === 'needs_category_confirmation' && !done

  async function choose(category: string) {
    setConfirming(true)
    try {
      await confirmCategory(result.garment_id, category)
      setDone(true)
      onToast('已确认品类，衣物入库成功', 'success')
      onChanged()
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="t-subtitle text-slate-900">识别结果</h3>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${
            needsConfirmation
              ? 'bg-amber-50 text-amber-700'
              : 'bg-emerald-50 text-emerald-700'
          }`}
        >
          {needsConfirmation ? '需要你确认品类' : '已入库'}
        </span>
      </div>

      <div className="mt-3 flex gap-3">
        <div className="h-24 w-20 shrink-0 overflow-hidden rounded-lg bg-slate-100">
          <img
            src={imageUrl(result.garment_id)}
            alt="衣物缩略图"
            className="h-full w-full object-cover"
            onError={(event) => {
              event.currentTarget.style.visibility = 'hidden'
            }}
          />
        </div>
        <dl className="min-w-0 flex-1 space-y-1 text-sm">
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">品类</dt>
            <dd className="truncate font-medium">
              {zhCategory(extracted.category)}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">细分</dt>
            <dd className="truncate">{asText(extracted.subcategory)}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">颜色</dt>
            <dd className="truncate">{zhColors(extracted.colors)}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">图案</dt>
            <dd className="truncate">{asText(extracted.pattern)}</dd>
          </div>
        </dl>
      </div>

      {needsConfirmation && (
        <div className="mt-4 rounded-xl bg-amber-50 p-3">
          <p className="mb-2 text-sm font-medium text-amber-900">
            这张图不太确定，你觉得是哪一类？
          </p>
          <div className="flex flex-wrap gap-2">
            {(options.length > 0 ? options : ['top', 'other'])
              .slice(0, 2)
              .map((option) => (
                <button
                  key={option}
                  type="button"
                  disabled={confirming}
                  onClick={() => void choose(option)}
                  className="flex-1 rounded-xl bg-white px-3 py-2 text-sm font-medium text-amber-900 shadow-sm ring-1 ring-amber-200 hover:bg-amber-100 disabled:opacity-50"
                >
                  {zhCategory(option)}
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function IngestTab({
  ownerId,
  onToast,
  onWardrobeChanged,
  compact = false,
}: IngestTabProps) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [results, setResults] = useState<GarmentCreateResult[]>([])
  const [compressInfo, setCompressInfo] = useState<string | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const waited = useElapsed(busy)

  function requireOwner(): boolean {
    if (ownerId) return true
    onToast('请先在顶部选择用户，或手动填入用户 ID', 'error')
    return false
  }

  async function handleFiles(fileList: FileList | null) {
    const files = Array.from(fileList ?? [])
    if (files.length === 0 || !requireOwner()) return
    setBusy(true)
    setLastError(null)
    setResults([])
    setCompressInfo(null)
    setProgress({ done: 0, total: files.length })

    try {
      const outcomes = await runPool(files, CONCURRENCY, async (file, index) => {
        const before = await readImageSize(file)
        const compressed = await compressImage(file)
        const after = await readImageSize(compressed)
        if (index === 0) {
          setCompressInfo(
            `图片压缩：${before.width}×${before.height} → ${after.width}×${after.height}`,
          )
        }
        const response = await ingestImage(compressed, ownerId)
        setProgress((current) => ({ ...current, done: current.done + 1 }))
        return response
      })

      const ok = outcomes.filter((item) => item.ok)
      const failed = outcomes.filter((item) => !item.ok)
      setResults(
        ok.flatMap((item) => (item.value ? [item.value] : [])),
      )
      onWardrobeChanged()

      if (failed.length === 0) {
        onToast(
          files.length === 1
            ? '识别成功，衣物已入库'
            : `全部完成：${ok.length} 件衣物已入库`,
          'success',
        )
      } else {
        const firstError = failed[0]?.error ?? '未知错误'
        setLastError(
          `成功 ${ok.length} 件，失败 ${failed.length} 件。首个失败原因：${firstError}`,
        )
        onToast(
          `部分失败：成功 ${ok.length} 件，失败 ${failed.length} 件`,
          'error',
        )
      }
    } catch (error) {
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
      setProgress({ done: 0, total: 0 })
    }
  }

  async function handleUrl() {
    const value = url.trim()
    if (!value) {
      onToast('请先粘贴商品链接', 'error')
      return
    }
    if (!requireOwner()) return
    setBusy(true)
    setLastError(null)
    try {
      const response = await ingestUrl(value, ownerId)
      setResults([response])
      onWardrobeChanged()
      onToast('链接识别完成', 'success')
    } catch (error) {
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <div className="card-soft p-4">
        {!compact && (
          <>
            <h2 className="t-display">添加衣物</h2>
            <p className="t-meta mt-1">
              可以一次选择多张图片，最多同时识别 {CONCURRENCY} 张；
              上传前会自动压缩到最长边 1024px。
            </p>
          </>
        )}

        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => void handleFiles(event.target.files)}
        />
        {/* U5：柔和圆角 + 淡色衣物线框底纹，拖入时整块高亮 */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => !busy && fileInput.current?.click()}
          onKeyDown={(event) => {
            if ((event.key === 'Enter' || event.key === ' ') && !busy) {
              fileInput.current?.click()
            }
          }}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            void handleFiles(event.dataTransfer.files)
          }}
          className="relative mt-4 flex cursor-pointer flex-col items-center justify-center gap-1.5 overflow-hidden rounded-2xl border px-4 py-9 text-center transition"
          style={{
            borderColor: dragging ? 'var(--sage)' : 'var(--line)',
            background: dragging ? 'var(--sage-soft)' : 'var(--surface)',
            boxShadow: 'var(--shadow-1)',
          }}
        >
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 h-full w-full"
            style={{ opacity: 0.06 }}
          >
            <defs>
              <pattern id="aidrobe-hanger" width="72" height="72" patternUnits="userSpaceOnUse">
                <path
                  d="M36 18 v6 M36 24 L20 37 H52 Z M20 37 h32 v13 H20 z"
                  fill="none"
                  stroke="var(--sage-deep)"
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#aidrobe-hanger)" />
          </svg>
          <span className="relative z-10 flex h-11 w-11 items-center justify-center rounded-full"
            style={{ background: 'var(--sage-soft)' }}>
            <UploadCloud size={23} strokeWidth={1.6} style={{ color: 'var(--sage-deep)' }} />
          </span>
          <span className="relative z-10 font-medium" style={{ fontSize: 14, color: 'var(--ink)' }}>
            {busy ? '正在识别衣物属性…' : '把图片拖到这里，或点击选择'}
          </span>
          <span className="t-meta relative z-10">
            {busy
              ? progress.total > 1
                ? `已完成 ${progress.done}/${progress.total} 件 · ${describeWait(waited)}`
                : describeWait(waited)
              : '支持一次多张，自动压缩'}
          </span>
          {busy && progress.total > 0 && (
            <div
              className="relative z-10 mt-2 h-1.5 w-full max-w-xs overflow-hidden rounded-full"
              style={{ background: 'var(--line)' }}
            >
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${Math.round((progress.done / progress.total) * 100)}%`,
                  background: 'var(--sage-deep)',
                }}
              />
            </div>
          )}
        </div>

        <div className="my-4 flex items-center gap-3 t-meta">
          <span className="h-px flex-1" style={{ background: 'var(--line)' }} />
          或者
          <span className="h-px flex-1" style={{ background: 'var(--line)' }} />
        </div>

        <label className="block text-xs font-medium text-slate-600" htmlFor="ingest-url">
          粘贴商品链接
        </label>
        <div className="relative mt-2">
          <input
            id="ingest-url"
            type="url"
            value={url}
            disabled={busy}
            onChange={(event) => setUrl(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !busy) void handleUrl()
            }}
            placeholder="粘贴商品链接 https://…"
            className="ui-field w-full py-3 pr-20"
            style={{ paddingRight: 76 }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleUrl()}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-lg px-3 py-1.5 text-white transition disabled:cursor-not-allowed disabled:opacity-50"
            style={{ background: 'var(--sage-deep)', fontSize: 12.5 }}
          >
            识别
          </button>
        </div>

        {compressInfo && (
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            {compressInfo}
          </p>
        )}
      </div>

      {lastError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm">
          <p className="font-medium text-rose-800">这次入库有问题</p>
          <p className="mt-1 break-all text-xs leading-5 text-rose-700">
            {lastError}
          </p>
          <p className="mt-1.5 text-xs text-rose-500">
            「设置 → 诊断」里有每次请求的状态码和耗时。
          </p>
        </div>
      )}

      {busy && results.length === 0 && <CardSkeleton />}

      {results.length > 0 && (
        <div className="space-y-3">
          {results.map((result) => (
            <ResultCard
              key={result.garment_id}
              result={result}
              onToast={onToast}
              onChanged={onWardrobeChanged}
            />
          ))}
        </div>
      )}

    </section>
  )
}
