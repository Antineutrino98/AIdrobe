/** 推荐 / 穿着策略：用户自己决定「穿过的衣服多久能再出现」等偏好。 */
import { useEffect, useState } from 'react'
import { errorMessage, getPolicy, putPolicy } from '../api/client'
import type { PushToast } from './Toast'
import type { RecommendPolicy } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  userId: string
  onToast: PushToast
}

const REWEAR_OPTIONS: { value: RecommendPolicy['rewear']; label: string }[] = [
  { value: 'anytime', label: '随时可再穿（刚穿过、没洗也能接着穿）' },
  { value: 'no_yesterday', label: '昨天或今天穿过的，今天不再推荐' },
  { value: 'rest_2', label: '穿过后至少隔 2 天' },
  { value: 'rest_3', label: '穿过后至少隔 3 天' },
  { value: 'rest_7', label: '穿过后至少隔 7 天' },
]

const DRESS_CODE_OPTIONS: {
  value: RecommendPolicy['dress_code']
  label: string
  hint: string
}[] = [
  { value: 'business', label: '很商务', hint: '衬衫、西裤、西装外套为主' },
  { value: 'smart_casual', label: '商务休闲', hint: '可以稍微放松，针织和干净运动鞋都行' },
  { value: 'free', label: '无着装要求', hint: '怎么舒服怎么穿，不刻意提高正式度' },
]

const PREFER_OPTIONS: { value: RecommendPolicy['prefer']; label: string }[] = [
  { value: 'balanced', label: '均衡（不特别偏好）' },
  { value: 'least_worn', label: '优先推荐最近穿得少的' },
  { value: 'newest', label: '优先推荐最近入库的' },
]

const POOL_FALLBACK_OPTIONS: {
  value: RecommendPolicy['pool_fallback']
  label: string
  hint: string
}[] = [
  {
    value: 'strict',
    label: '严格：宁可少给或留空',
    hint: '正式度 / 保暖度不符合的单品一律不用；实在凑不出就返回 0 套。',
  },
  {
    value: 'moderate',
    label: '适中：条件太窄时逐级放宽（默认）',
    hint: '先按区间筛；候选太少就把区间逐级外扩一级，尽量给出搭配。',
  },
  {
    value: 'relaxed',
    label: '勉强推荐：放宽条件凑几套',
    hint: '区间筛完不够时放宽到全部可穿单品，尽量给出搭配。',
  },
]

const COLD_OPTIONS: {
  value: RecommendPolicy['cold_sensitivity']
  label: string
}[] = [
  { value: 'normal', label: '常规（按正常厚度）' },
  { value: 'mild', label: '有点怕冷（略偏厚）' },
  { value: 'strong', label: '很怕冷（明显偏厚、更保暖）' },
]

const HEAT_OPTIONS: {
  value: RecommendPolicy['heat_sensitivity']
  label: string
}[] = [
  { value: 'normal', label: '常规（按正常厚度）' },
  { value: 'mild', label: '有点怕热（略偏薄）' },
  { value: 'strong', label: '很怕热（明显偏薄、更透气）' },
]

const DEFAULT: RecommendPolicy = {
  rewear: 'anytime',
  prefer: 'balanced',
  outfit_count: 3,
  dress_code: 'smart_casual',
  pool_fallback: 'moderate',
  cold_sensitivity: 'normal',
  heat_sensitivity: 'normal',
}

export function PolicyDialog({ open, onClose, userId, onToast }: Props) {
  const [policy, setPolicy] = useState<RecommendPolicy>(DEFAULT)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open || !userId) return
    setBusy(true)
    void getPolicy(userId)
      .then(setPolicy)
      .catch((error) => onToast(errorMessage(error), 'error'))
      .finally(() => setBusy(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, userId])

  if (!open) return null

  async function save() {
    if (!userId) {
      onToast('请先在左侧选择一个用户', 'error')
      return
    }
    setBusy(true)
    try {
      setPolicy(await putPolicy(userId, policy))
      onToast('推荐策略已保存', 'success')
      onClose()
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
        <h2 className="text-base font-semibold text-slate-900">穿着与推荐策略</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          在搭配卡片上点「今天穿了」后，这一套里的每件衣服都会记下今天的穿着日期。
          该洗了、洗衣中的衣服始终不进推荐，不需要设置；「穿过一次」不算脏，
          能不能接着穿由下面第一项决定。策略按用户分别保存。
        </p>

        <div className="mt-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700" htmlFor="pool-fallback">
              库里没有符合要求的单品时
            </label>
            <select
              id="pool-fallback"
              disabled={busy}
              value={policy.pool_fallback ?? 'strict'}
              onChange={(event) =>
                setPolicy((current) => ({
                  ...current,
                  pool_fallback: event.target.value as RecommendPolicy['pool_fallback'],
                }))
              }
              className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            >
              {POOL_FALLBACK_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              {POOL_FALLBACK_OPTIONS.find(
                (item) => item.value === (policy.pool_fallback ?? 'moderate'),
              )?.hint}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-slate-700" htmlFor="cold-sensitivity">
                偏怕冷
              </label>
              <select
                id="cold-sensitivity"
                disabled={busy}
                value={policy.cold_sensitivity ?? 'normal'}
                onChange={(event) =>
                  setPolicy((current) => ({
                    ...current,
                    cold_sensitivity: event.target.value as RecommendPolicy['cold_sensitivity'],
                  }))
                }
                className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
              >
                {COLD_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700" htmlFor="heat-sensitivity">
                偏怕热
              </label>
              <select
                id="heat-sensitivity"
                disabled={busy}
                value={policy.heat_sensitivity ?? 'normal'}
                onChange={(event) =>
                  setPolicy((current) => ({
                    ...current,
                    heat_sensitivity: event.target.value as RecommendPolicy['heat_sensitivity'],
                  }))
                }
                className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
              >
                {HEAT_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700" htmlFor="rewear">
              刚穿过的衣服
            </label>
            <select
              id="rewear"
              disabled={busy}
              value={policy.rewear}
              onChange={(event) =>
                setPolicy((current) => ({
                  ...current,
                  rewear: event.target.value as RecommendPolicy['rewear'],
                }))
              }
              className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            >
              {REWEAR_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <span className="block text-xs font-medium text-slate-700">
              上班穿成什么样
            </span>
            <p className="mt-1 text-xs text-slate-500">
              只影响「上班」这个场合，约会、周末不受影响。
            </p>
            <div className="mt-2 space-y-1.5">
              {DRESS_CODE_OPTIONS.map((item) => (
                <label
                  key={item.value}
                  className={`flex cursor-pointer items-start gap-2.5 rounded-xl border px-3 py-2 transition ${
                    policy.dress_code === item.value
                      ? 'border-indigo-300 bg-indigo-50/60'
                      : 'border-slate-200 hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="dress-code"
                    disabled={busy}
                    checked={policy.dress_code === item.value}
                    onChange={() =>
                      setPolicy((current) => ({
                        ...current,
                        dress_code: item.value,
                      }))
                    }
                    className="mt-1"
                  />
                  <span>
                    <span className="block font-medium text-slate-800">
                      {item.label}
                    </span>
                    <span className="block text-xs text-slate-500">{item.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700" htmlFor="prefer">
              候选优先级
            </label>
            <select
              id="prefer"
              disabled={busy}
              value={policy.prefer}
              onChange={(event) =>
                setPolicy((current) => ({
                  ...current,
                  prefer: event.target.value as RecommendPolicy['prefer'],
                }))
              }
              className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            >
              {PREFER_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700" htmlFor="count">
              每次至少生成几套
            </label>
            <select
              id="count"
              disabled={busy}
              value={policy.outfit_count}
              onChange={(event) =>
                setPolicy((current) => ({
                  ...current,
                  outfit_count: Number(event.target.value),
                }))
              }
              className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            >
              {[3, 4, 5].map((count) => (
                <option key={count} value={count}>
                  {count} 套
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-slate-400">
              套数不足时直接返回已有结果，不会重复调用模型；这个数字会原样告诉搭配模型。
            </p>
          </div>
        </div>

        <div className="mt-5 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void save()}
            className="flex-1 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            保存
          </button>
          <button
            type="button"
            onClick={() => setPolicy(DEFAULT)}
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm text-slate-600"
          >
            恢复默认
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm text-slate-600"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
