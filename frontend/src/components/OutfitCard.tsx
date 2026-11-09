/** U2 搭配卡片：沉浸式大卡片、单品平铺拼图、Framer Motion 滑入。 */
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, RotateCcw, Shirt, ThumbsDown, ThumbsUp } from 'lucide-react'
import { imageUrl } from '../api/client'
import { zhCategory } from '../lib/i18n'
import type { PushToast } from './Toast'
import { Badge, Button, Dialog } from './ui'
import type {
  FeedbackSignal,
  Recommendation,
  WearState,
} from '../types'

interface OutfitCardProps {
  outfit: Recommendation
  index: number
  busy: boolean
  onFeedback: (
    outfit: Recommendation,
    index: number,
    signal: FeedbackSignal,
    wearState?: WearState,
    reason?: string,
  ) => Promise<void>
  onRegenerate: () => void
  onToast: PushToast
}

// U1 之后是品类大类标签，旧名一并兼容展示。
const GROUP_LABELS: Record<string, string> = {
  tops: '上装', one_piece: '连衣裙 / 套装', bottoms: '下装',
  outer: '外套', shoes: '鞋', accessory: '配饰', other: '其他',
  inner: '上装', mid: '中层', bottom: '下装',
}

// 00:51：用户点「不喜欢」时可选的原因，写进 feedback.free_text 让 M5 学习。
const DISLIKE_REASONS: string[] = [
  '太热了，这个温度穿不住',
  '太冷了，这个温度穿不住',
  '和今天的场合不搭',
  '颜色或风格不协调',
  '版型 / 厚薄叠不到一起',
  '和衣橱里已有的重复',
  '就是不喜欢这个风格',
]

export function OutfitCard({
  outfit,
  index,
  busy,
  onFeedback,
  onRegenerate,
  onToast,
}: OutfitCardProps) {
  const [showWearSheet, setShowWearSheet] = useState(false)
  const [showDislikeSheet, setShowDislikeSheet] = useState(false)
  const [pending, setPending] = useState(false)
  const [brokenImages, setBrokenImages] = useState<Set<string>>(new Set())

  const disabled = busy || pending
  const items = outfit.items ?? []

  // 21:56 反馈：WebView2 里横向滚动只能靠键盘，鼠标无从下手。
  // 这里补齐三种鼠标操作：按住拖拽、点左右箭头、滚轮横向滚。
  const scrollerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef({ active: false, startX: 0, startLeft: 0 })
  const [canScroll, setCanScroll] = useState(false)

  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const update = () => setCanScroll(el.scrollWidth > el.clientWidth + 8)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [items.length])

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    const el = scrollerRef.current
    if (!el || el.scrollWidth <= el.clientWidth) return
    dragRef.current = {
      active: true,
      startX: event.clientX,
      startLeft: el.scrollLeft,
    }
    el.setPointerCapture(event.pointerId)
    el.style.cursor = 'grabbing'
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const el = scrollerRef.current
    if (!el || !dragRef.current.active) return
    el.scrollLeft = dragRef.current.startLeft - (event.clientX - dragRef.current.startX)
  }

  function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    dragRef.current.active = false
    const el = scrollerRef.current
    if (!el) return
    el.style.cursor = ''
    try {
      el.releasePointerCapture(event.pointerId)
    } catch {
      // 指针已经释放过，忽略
    }
  }

  function onWheel(event: ReactWheelEvent<HTMLDivElement>) {
    const el = scrollerRef.current
    if (!el || el.scrollWidth <= el.clientWidth) return
    if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      el.scrollLeft += event.deltaY
    }
  }

  function nudge(delta: number) {
    scrollerRef.current?.scrollBy({ left: delta, behavior: 'smooth' })
  }

  async function submit(
    signal: FeedbackSignal,
    wearState?: WearState,
    reason?: string,
  ) {
    setPending(true)
    try {
      await onFeedback(outfit, index, signal, wearState, reason)
      setShowWearSheet(false)
      setShowDislikeSheet(false)
    } catch {
      onToast('操作失败，请重试', 'error')
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <motion.article
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: index * 0.07, ease: 'easeOut' }}
        className="card-soft mx-auto w-full overflow-hidden"
      >
        {/* 21:56：恢复与上方表单、提示栏一致的宽度；图片 2 倍大，可鼠标侧滑。 */}
        <div className="relative" style={{ background: 'var(--surface-2)' }}>
        <div
          ref={scrollerRef}
          className="no-scrollbar flex cursor-grab gap-2 overflow-x-auto p-3"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
        >
          <div className="mx-auto flex w-max flex-nowrap gap-2">
          {items.length === 0 ? (
            <div className="py-8 text-center t-meta">这套没有单品信息</div>
          ) : (
            items.map((item) => (
              <div
                key={item.garment_id}
                className="relative overflow-hidden rounded-lg"
                style={{ background: 'var(--surface)', width: 256, height: 336, flex: '0 0 auto' }}
              >
                <div className="h-full w-full">
                  {brokenImages.has(item.garment_id) ? (
                    <div className="flex h-full w-full items-center justify-center t-caption">
                      无图片
                    </div>
                  ) : (
                    <img
                      src={imageUrl(item.garment_id)}
                      alt={zhCategory(item.category ?? '衣物')}
                      className="h-full w-full object-contain"
                      loading="lazy"
                      onError={() =>
                        setBrokenImages((current) => {
                          const next = new Set(current)
                          next.add(item.garment_id)
                          return next
                        })
                      }
                    />
                  )}
                </div>
                <span
                  className="absolute bottom-1 left-1 rounded-md px-1.5 py-0.5 text-white"
                  style={{ fontSize: 10, background: 'rgba(45,42,38,0.68)' }}
                >
                  {GROUP_LABELS[item.slot ?? ''] ?? zhCategory(item.category) ?? '单品'}
                </span>
              </div>
            ))
          )}
          </div>
        </div>
        {canScroll && (
          <>
            <button
              type="button"
              aria-label="向左滑动"
              onClick={() => nudge(-300)}
              className="absolute left-2 top-1/2 z-10 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border shadow-md"
              style={{ background: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--ink)' }}
            >
              <ChevronLeft size={18} />
            </button>
            <button
              type="button"
              aria-label="向右滑动"
              onClick={() => nudge(300)}
              className="absolute right-2 top-1/2 z-10 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border shadow-md"
              style={{ background: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--ink)' }}
            >
              <ChevronRight size={18} />
            </button>
          </>
        )}
        </div>

        <div className="p-4">
          <header className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="t-title">{outfit.style_direction || `搭配 ${index + 1}`}</h3>
              <p className="t-meta t-num mt-1">
                匹配度 {outfit.match_score ?? 0}/10 · {items.length} 件单品
              </p>
            </div>
            <Badge tone="sage" className="t-num shrink-0">
              第 {index + 1} 套
            </Badge>
          </header>

          <p className="t-body mt-3" style={{ color: 'var(--ink-2)' }}>
            {outfit.reasoning || '（这套没有给出理由）'}
          </p>

          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Button variant="soft" disabled={disabled} onClick={() => void submit('liked')}>
              <ThumbsUp size={14} /> 喜欢
            </Button>
            <Button variant="outline" disabled={disabled} onClick={() => setShowDislikeSheet(true)}>
              <ThumbsDown size={14} /> 不喜欢
            </Button>
            <Button variant="outline" disabled={disabled} onClick={() => setShowWearSheet(true)}>
              <Shirt size={14} /> 今天穿了
            </Button>
            <Button variant="outline" disabled={disabled} onClick={onRegenerate}>
              <RotateCcw size={14} /> 换一套
            </Button>
          </div>
        </div>
      </motion.article>

      {/* 00:51：点「不喜欢」先问原因，原因写进 free_text 交给偏好引擎 */}
      <Dialog
        open={showDislikeSheet}
        onOpenChange={setShowDislikeSheet}
        title="哪里不合适？"
        description="选一个原因，会记进你的偏好；这套搭配不会从页面上消失。"
        width="max-w-sm"
      >
        <div className="space-y-2">
          {DISLIKE_REASONS.map((reason) => (
            <Button
              key={reason}
              variant="soft"
              className="w-full justify-start"
              disabled={disabled}
              onClick={() => void submit('disliked', undefined, reason)}
            >
              {reason}
            </Button>
          ))}
          <Button
            variant="ghost"
            className="w-full"
            onClick={() => void submit('disliked')}
          >
            不填原因，直接不喜欢
          </Button>
        </div>
      </Dialog>

      {/* 「今天穿了」二选一：Radix Dialog，保留原来的默认「还干净」 */}
      <Dialog
        open={showWearSheet}
        onOpenChange={setShowWearSheet}
        title="今天穿了这套，衣服状态？"
        description="默认「还干净」，会记为穿过一次；之后仍可进入推荐。"
        width="max-w-xs"
      >
        <div className="space-y-2">
          <Button
            variant="primary"
            className="w-full"
            disabled={disabled}
            onClick={() => void submit('worn', 'worn_once')}
          >
            还干净（默认）
          </Button>
          <Button
            variant="soft"
            className="w-full"
            disabled={disabled}
            onClick={() => void submit('worn', 'dirty')}
          >
            该洗了
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => setShowWearSheet(false)}>
            取消
          </Button>
        </div>
      </Dialog>
    </>
  )
}
