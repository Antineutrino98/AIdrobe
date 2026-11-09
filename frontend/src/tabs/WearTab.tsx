/** M6 Tab 2「穿什么」：场景 → 推荐 → 3-5 套卡片 → 反馈 / 换一套。 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { CloudSun, Layers } from 'lucide-react'
import { errorMessage, recommend, sendFeedback } from '../api/client'
import { OutfitCard } from '../components/OutfitCard'
import { CardSkeleton } from '../components/Skeleton'
import type { PushToast } from '../components/Toast'
import { buildChatterLines } from '../lib/chatter'
import { zhWeather } from '../lib/i18n'
import { describeWait, useElapsed } from '../lib/useElapsed'
import type {
  FeedbackSignal,
  Recommendation,
  RecommendPayload,
  Scene,
  WardrobeScope,
  WearState,
} from '../types'

interface WearTabProps {
  ownerId: string
  scope: WardrobeScope
  /** 已被否定/穿过、下次不该再出现的组合指纹（由 App 跨 Tab 保存）。 */
  excluded: string[]
  onExclude: (fingerprint: string) => void
  onToast: PushToast
}

const SCENES: { value: Scene; label: string }[] = [
  { value: 'work', label: '上班' },
  { value: 'date', label: '约会' },
  { value: 'weekend', label: '周末' },
  { value: 'travel', label: '出行' },
  { value: 'formal-event', label: '正式场合' },
  { value: 'free_text', label: '自己描述' },
]

/** 用「单品 id 排序拼接」识别同一套组合，与顺序无关。 */
export function outfitFingerprint(outfit: Recommendation): string {
  return (outfit.items ?? [])
    .map((item) => item.garment_id)
    .sort()
    .join('|')
}

type LocationMode = 'none' | 'city'

export function WearTab({
  ownerId,
  scope,
  excluded,
  onExclude,
  onToast,
}: WearTabProps) {
  const [scene, setScene] = useState<Scene>('work')
  const [freeText, setFreeText] = useState('')
  const [locationMode, setLocationMode] = useState<LocationMode>('none')
  const [city, setCity] = useState('')
  const [temperature, setTemperature] = useState('')
  const [busy, setBusy] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [outfits, setOutfits] = useState<Recommendation[]>([])
  const [notes, setNotes] = useState('')
  const [hasSearched, setHasSearched] = useState(false)
  // 错误要留在页面上，而不是只闪 3 秒的 toast。
  const [lastError, setLastError] = useState<string | null>(null)
  const [poolNotes, setPoolNotes] = useState<string[]>([])
  const [weatherSummary, setWeatherSummary] = useState<{
    main: string
    hint: string
  } | null>(null)
  const waited = useElapsed(busy)
  // U5：等待期间逐条渐显「陪聊」文案，纯前端，不额外调用模型。
  const chatterLines = useMemo(
    () => buildChatterLines({ city, scene, temperature }),
    [city, scene, temperature],
  )
  const [chatterCount, setChatterCount] = useState(1)
  useEffect(() => {
    if (!busy) {
      setChatterCount(1)
      return
    }
    const timer = window.setInterval(() => {
      setChatterCount((n) => Math.min(n + 1, chatterLines.length))
    }, 9_000)
    return () => window.clearInterval(timer)
  }, [busy, chatterLines.length])

  // async 回调里要读到最新的排除列表，用 ref 同步。
  const excludedRef = useRef(excluded)
  excludedRef.current = excluded

  // 00:51：切语言后清掉上一次请求留下的模型文本，中文界面不再残留英文理由。
  useEffect(() => {
    const onLanguageChange = () => {
      if (busy) return // 不打断正在进行的请求
      setOutfits([])
      setNotes('')
      setWeatherSummary(null)
      setPoolNotes([])
      setHasSearched(false)
    }
    window.addEventListener('aidrobe:language-change', onLanguageChange)
    return () => window.removeEventListener('aidrobe:language-change', onLanguageChange)
  }, [busy])

  // 21:56 反馈：一次搭配最长要等两分钟，必须能中途取消。
  const abortRef = useRef<AbortController | null>(null)
  function cancelPending() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    onToast('已取消本次搭配', 'info')
  }

  function buildPayload(): RecommendPayload | null {
    if (!ownerId) {
      onToast('请先在顶部选择用户，或手动填入用户 ID', 'error')
      return null
    }
    let location: RecommendPayload['location'] = null
    if (locationMode === 'city' && city.trim()) {
      location = { city: city.trim() }
    }
    // 01:30：用户会填「0-8」「18」「0~8℃」这类值。以前直接 Number()
    // 会把区间变成 NaN → JSON 里变 null → 后端「温度未知」。
    const rawTemp = temperature.trim().replace(/[℃°]/g, '').replace(/\s/g, '')
    const range = rawTemp.match(/^(-?\d+(?:\.\d+)?)[-~～—](-?\d+(?:\.\d+)?)$/)
    let temperatureOverride: number | null = null
    let temperatureMin: number | null = null
    let temperatureMax: number | null = null
    if (range) {
      const a = Number(range[1])
      const b = Number(range[2])
      temperatureMin = Math.min(a, b)
      temperatureMax = Math.max(a, b)
    } else if (rawTemp) {
      const value = Number(rawTemp)
      if (Number.isFinite(value)) temperatureOverride = value
    }
    return {
      owner_id: ownerId,
      scene,
      free_text: freeText.trim() || null,
      location,
      temperature_override: temperatureOverride,
      temperature_override_min: temperatureMin,
      temperature_override_max: temperatureMax,
      wardrobe_scope: scope,
    }
  }

  /**
   * 请求推荐并过滤掉已排除的组合。
   * M3 的 /recommend 目前没有 exclude 参数，所以这里是**前端过滤**；
   * 若过滤后不足 3 套，最多再请求 2 次，仍不足则如实展示已有结果。
   */
  async function fetchRecommendations(isRegenerate: boolean) {
    const payload = buildPayload()
    if (!payload) return
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setHasSearched(true)
    setLastError(null)
    try {
      // v1.0 硬契约：禁止多轮重试搭配。单次约 80–130 秒，串三次就是十分钟；
      // 套数不足如实返回已有结果，不再自动补调用。
      const response = await recommend(payload, controller.signal)
      const usedSessionId = response.session_id || ''
      const usedNotes = response.layering_plan?.layering_hint ?? ''
      const usedWeatherInfo: {
        temperature?: number | null
        temperature_max?: number | null
        temperature_min?: number | null
        city?: string | null
        condition?: string | null
      } | null = response.weather ?? null
      const usedPoolNotes: string[] = response.pool_notes ?? []
      const collected: Recommendation[] = (response.recommendations ?? []).filter(
        (outfit) => !excludedRef.current.includes(outfitFingerprint(outfit)),
      )
      // 天气摘要：第一行客观事实，第二行给出这条件下的搭配取舍。
      // 不做机械填空，按温差和套数把话说顺。
      const w = usedWeatherInfo
      const hasRange =
        typeof w?.temperature_min === 'number' &&
        typeof w?.temperature_max === 'number'
      if (!w) {
        setWeatherSummary(null)
      } else {
        const place = w.city || city.trim() || '当地'
        const low = hasRange ? Math.round(w.temperature_min!) : null
        const high = hasRange ? Math.round(w.temperature_max!) : null
        const now = typeof w.temperature === 'number' ? Math.round(w.temperature) : null
        const tempText =
          low !== null && high !== null
            ? `${low}–${high}℃`
            : now !== null
              ? `${now}℃`
              : '温度未知'
        const parts = [place, tempText, zhWeather(w.condition)]
        if (low !== null && high !== null) {
          parts.push(`温差 ${high - low}℃`)
        }
        const main = parts.join(' · ')

        const span = low !== null && high !== null ? high - low : null
        const count = collected.length
        const style = collected[0]?.style_direction
        let hint: string
        if (span !== null && span >= 8) {
          hint =
            `早晚差 ${span}℃，所以搭配里留了可以随时增减的外层` +
            (style ? `，主推「${style}」这类方向` : '') +
            `。这次给你配了 ${count} 套。`
        } else if (span !== null && span <= 3) {
          hint =
            `全天温度平稳，不需要叠穿` +
            (style ? `，主推「${style}」这类方向` : '') +
            `。这次给你配了 ${count} 套。`
        } else {
          hint =
            (style ? `按「${style}」这类方向` : '按当天温度') +
            `配了 ${count} 套，薄外套够用。`
        }
        setWeatherSummary({ main, hint })
      }
      setSessionId(usedSessionId)
      setPoolNotes(usedPoolNotes)
      setNotes(usedNotes)
      setOutfits(collected)
      if (collected.length === 0) {
        onToast('暂时没有可用的搭配，换个场景或衣橱范围试试', 'info')
      } else if (isRegenerate) {
        onToast('已换一批搭配', 'success')
      }
    } catch (error) {
      if (controller.signal.aborted) return
      setLastError(errorMessage(error))
      onToast(errorMessage(error), 'error')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  /**
   * 00:51 反馈：点「不喜欢」不再把整套搭配从页面上抹掉 —— 用户只想说明
   * 为什么不喜欢，不想一点击结果就没了。原因写进 free_text，交给 M5 偏好引擎。
   */
  async function handleFeedback(
    outfit: Recommendation,
    index: number,
    signal: FeedbackSignal,
    wearState?: WearState,
    reason?: string,
  ) {
    const shouldRemove = signal !== 'disliked'
    const snapshot = outfits
    if (shouldRemove) {
      setOutfits((current) => current.filter((_, position) => position !== index))
    }
    try {
      await sendFeedback({
        session_id: sessionId,
        outfit_index: index,
        signal,
        wear_state: wearState,
        free_text: reason,
      })
      // disliked 和 worn 都不该在下次推荐里再出现（M6 验收 2 / 3）。
      if (signal === 'disliked' || signal === 'worn') {
        onExclude(outfitFingerprint(outfit))
      }
      onToast(
        signal === 'disliked'
          ? (reason ? `已记录：${reason}，之后不再推荐这套` : '已记录：不喜欢，之后不再推荐这套')
          : signal === 'liked'
            ? '已记录：喜欢'
            : wearState === 'dirty'
              ? '已记录：今天穿了，标记为该洗了'
              : '已记录：今天穿了',
        'success',
      )
    } catch (error) {
      if (shouldRemove) setOutfits(snapshot) // 回滚
      onToast(errorMessage(error), 'error')
    }
  }

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="t-title text-slate-900">今天穿什么</h2>

        <label className="mt-3 block text-xs font-medium text-slate-600" htmlFor="scene">
          场景
        </label>
        <select
          id="scene"
          value={scene}
          disabled={busy}
          onChange={(event) => setScene(event.target.value as Scene)}
          className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
        >
          {SCENES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>

        <label className="mt-3 block text-xs font-medium text-slate-600" htmlFor="free-text">
          补充说明（可选）
        </label>
        <input
          id="free-text"
          value={freeText}
          disabled={busy}
          onChange={(event) => setFreeText(event.target.value)}
          placeholder="约会但不想太正式"
          className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
        />

        <div className="mt-3">
          <span className="block text-xs font-medium text-slate-600">位置 / 天气</span>
          <div className="mt-1.5 flex gap-2">
            {(
              [
                ['none', '不填'],
                ['city', '城市'],
              ] as [LocationMode, string][]
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                disabled={busy}
                onClick={() => setLocationMode(mode)}
                className={`flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition disabled:opacity-50 ${
                  locationMode === mode
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {locationMode === 'city' && (
            <input
              value={city}
              disabled={busy}
              onChange={(event) => setCity(event.target.value)}
              placeholder="输入城市名，自动查询当地温度"
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
            />
          )}
        </div>

        <label className="mt-3 block text-xs font-medium text-slate-600" htmlFor="temp">
          温度覆盖（℃，可选）
        </label>
        <input
          id="temp"
          value={temperature}
          disabled={busy}
          onChange={(event) => setTemperature(event.target.value)}
          placeholder="18"
          inputMode="numeric"
          className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
        />

        <button
          type="button"
          disabled={busy}
          onClick={() => void fetchRecommendations(false)}
          className="mt-4 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? describeWait(waited) : '✨ 给我搭配'}
        </button>
      </div>

      {lastError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm">
          <p className="font-medium text-rose-800">这次请求失败了</p>
          <p className="mt-1 break-all text-xs leading-5 text-rose-700">
            {lastError}
          </p>
          <p className="mt-1.5 text-xs text-rose-500">
            可以点上面按钮重试；右上角「请求日志」里有每次请求的状态码和耗时。
          </p>
        </div>
      )}

      {/* U2：显眼的叠穿提示，放在结果最上方 */}
      {notes && (
        <div
          className="flex items-start gap-3 rounded-xl px-4 py-3"
          style={{ background: 'var(--sage-soft)', color: 'var(--sage-deep)' }}
        >
          <Layers size={19} className="mt-0.5 shrink-0" />
          <div>
            <p className="t-eyebrow" style={{ color: 'var(--sage-deep)' }}>
              搭配提示
            </p>
            <p className="t-body-sm mt-0.5 font-medium">{notes}</p>
          </div>
        </div>
      )}

      {weatherSummary && (
        <div
          className="card-soft flex items-start gap-3 px-4 py-3"
        >
          <CloudSun size={18} className="mt-0.5 shrink-0" style={{ color: 'var(--clay)' }} />
          <div className="min-w-0">
            <p className="t-subtitle t-num">{weatherSummary.main}</p>
            <p className="t-meta mt-0.5" style={{ lineHeight: 1.6 }}>
              {weatherSummary.hint}
            </p>
          </div>
        </div>
      )}

      {poolNotes.length > 0 && (
        <div
          className="rounded-xl px-3.5 py-2.5"
          style={{ background: '#f6edda', color: '#8b6d33' }}
        >
          <p className="font-medium" style={{ fontSize: 12.5 }}>
            候选池说明
          </p>
          <p className="mt-0.5" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
            {poolNotes.join('；')}
          </p>
        </div>
      )}

      {busy && (
        <div className="space-y-3">
          <div className="card-soft p-4">
            <div className="flex items-center gap-2" style={{ color: 'var(--sage-deep)' }}>
              <Layers size={17} />
              <span className="font-medium" style={{ fontSize: 14 }}>
                造型师正在翻衣橱…
              </span>
            </div>
            <div className="mt-3 space-y-2">
              {chatterLines.slice(0, chatterCount).map((line, index) => (
                <motion.p
                  key={index}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35 }}
                  className="t-body"
                  style={{ color: 'var(--ink-2)' }}
                >
                  {line}
                </motion.p>
              ))}
            </div>
            <p className="t-meta mt-3">{describeWait(waited)}</p>
            <button
              type="button"
              onClick={cancelPending}
              className="mt-3 rounded-lg border px-3 py-1.5 transition hover:opacity-75"
              style={{ borderColor: 'var(--line)', color: 'var(--ink-2)', fontSize: 12 }}
            >
              取消本次搭配
            </button>
          </div>
          <CardSkeleton />
        </div>
      )}

      {!busy && outfits.length > 0 && (
        /* 指令区只有 2/6 宽，多列会把卡片挤成竖条、按钮竖排、长理由被截断。
           改成单列，每套完整展示。 */
        <div className="space-y-4">
          {outfits.map((outfit, index) => (
            <OutfitCard
              key={`${sessionId}-${outfitFingerprint(outfit)}-${index}`}
              outfit={outfit}
              index={index}
              busy={busy}
              onFeedback={handleFeedback}
              onRegenerate={() => void fetchRecommendations(true)}
              onToast={onToast}
            />
          ))}
        </div>
      )}

      {!busy && hasSearched && outfits.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm">
          <p className="font-medium" style={{ color: 'var(--ink)' }}>
            衣服不足 / 没有合适搭配
          </p>
          <p className="mt-1.5 text-slate-500">
            按当前温度、场合和衣橱里能穿的单品，这次没能配出合适的搭配。
            可以补充衣物、换个场景，或在「推荐策略」里改成「勉强推荐」。
          </p>
        </div>
      )}
    </section>
  )
}
