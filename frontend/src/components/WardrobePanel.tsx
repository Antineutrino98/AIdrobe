/**
 * U2 我的衣橱：瀑布流网格 + 筛选栏 + 悬停操作。
 *
 * 保留 v1.0 的两条硬约定：按中文品类分组可折叠、详情全中文。
 * 衣橱卡片悬停时浮出「标记待洗 / 洗干净了」与「删除」。
 */
import { useEffect, useMemo, useState } from 'react'
import { Droplets, RefreshCw, Trash2 } from 'lucide-react'
import {
  deleteGarment,
  errorMessage,
  imageUrl,
  listGarments,
  setWearState,
} from '../api/client'
import type { PushToast } from './Toast'
import { Badge, Button, Dialog, Segmented } from './ui'
import {
  DIMENSION_ZH,
  zhCategory,
  zhColors,
  zhList,
  zhSubcategory,
  zhText,
  zhWearState,
} from '../lib/i18n'
import type { WardrobeItem, WardrobeScope } from '../types'

interface Props {
  ownerId: string
  scope: WardrobeScope
  refreshKey: number
  onToast: PushToast
  inTab?: boolean
}

// ---------------------------------------------------------------- 中文色点
const COLOR_HEX: Record<string, string> = {
  黑色: '#2b2b2b', 白色: '#fafafa', 米白: '#f2ece0', 象牙白: '#f5eedd',
  米色: '#e3d5bd', 灰色: '#9a9a9a', 浅灰: '#c8c8c8', 深灰: '#5a5a5a',
  炭灰: '#4a4a4a', 藏青: '#2c3a55', 蓝色: '#4a6fa5', 浅蓝: '#a8c4dd',
  牛仔蓝: '#5b7b9e', 红色: '#b4483f', 酒红: '#7b3540', 粉色: '#e2b6bd',
  浅粉: '#f0d5d7', 绿色: '#5f8060', 橄榄绿: '#7b8455', 棕色: '#7b5b43',
  卡其: '#b9a581', 驼色: '#b99b74', 黄色: '#d9bd63', 紫色: '#8a739e',
  橙色: '#cf8b53', 银色: '#c4c7c9', 金色: '#c9ab62', 多色: '#a8a29a',
}

function colorDots(colors: unknown): { name: string; hex: string }[] {
  if (!Array.isArray(colors)) return []
  const out: { name: string; hex: string }[] = []
  for (const entry of colors) {
    const name =
      typeof entry === 'string'
        ? entry
        : entry && typeof entry === 'object'
          ? String((entry as Record<string, unknown>).name ?? '')
          : ''
    const zh = zhText(name)
    if (!zh || zh === '—') continue
    out.push({ name: zh, hex: (entry as Record<string, unknown>)?.hex as string || COLOR_HEX[zh] || '#b8ada0' })
    if (out.length >= 3) break
  }
  return out
}

// ---------------------------------------------------------------- 品类筛选
type GroupFilter = 'all' | 'tops' | 'bottoms' | 'dress' | 'outer' | 'shoes'

const GROUP_OPTIONS: { value: GroupFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'tops', label: '上装' },
  { value: 'bottoms', label: '下装' },
  { value: 'dress', label: '裙装' },
  { value: 'outer', label: '外套' },
  { value: 'shoes', label: '鞋' },
]

const TOPS = new Set(['top', 't_shirt', 'shirt', 'sweater', 'hoodie', 'vest', 'tank_top', 'base_layer'])
const BOTTOMS = new Set(['bottom', 'trousers', 'jeans', 'shorts', 'socks'])
const DRESS = new Set(['dress', 'jumpsuit', 'skirt', 'robe', 'ceremonial', 'suit'])
const OUTER = new Set(['outerwear', 'coat', 'jacket', 'blazer', 'down_jacket', 'cape', 'raincoat'])
const SHOES = new Set(['shoes', 'sneakers', 'boots', 'sandals', 'slippers'])

function matchesGroup(category: string, group: GroupFilter): boolean {
  if (group === 'all') return true
  if (group === 'tops') return TOPS.has(category)
  if (group === 'bottoms') return BOTTOMS.has(category)
  if (group === 'dress') return DRESS.has(category)
  if (group === 'outer') return OUTER.has(category)
  return SHOES.has(category)
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="flex gap-3 border-b py-1.5 last:border-0"
      style={{ borderColor: 'var(--line)' }}
    >
      <dt className="w-20 shrink-0" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
        {label}
      </dt>
      <dd className="min-w-0 flex-1 break-words" style={{ fontSize: 13 }}>
        {value}
      </dd>
    </div>
  )
}

// ---------------------------------------------------------------- 详情弹窗
function DetailDialog({
  item,
  onClose,
  onDeleted,
  onChanged,
  onToast,
}: {
  item: WardrobeItem
  onClose: () => void
  onDeleted: () => void
  onChanged: () => void
  onToast: PushToast
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  async function remove() {
    setBusy(true)
    try {
      await deleteGarment(item.id)
      onToast('这件衣服已从衣橱移除', 'success')
      onDeleted()
      onClose()
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  async function toggleWash() {
    setBusy(true)
    try {
      const next = item.wear_state === 'clean' ? 'dirty' : 'clean'
      await setWearState(item.id, next)
      onToast(next === 'dirty' ? '已标记为待洗，推荐里不会再出现' : '已标记为干净', 'success')
      onChanged()
      onClose()
    } catch (error) {
      onToast(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  const categoryZh = zhCategory(item.category)

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={categoryZh}
      description={zhSubcategory(item.subcategory, categoryZh)}
      width="max-w-lg"
    >
      <div className="flex items-start gap-4">
        <div
          className="h-40 w-[120px] shrink-0 overflow-hidden rounded-xl"
          style={{ background: 'var(--surface-2)' }}
        >
          <img
            src={imageUrl(item.image_url)}
            alt={categoryZh}
            className="h-full w-full object-cover"
            onError={(event) => {
              event.currentTarget.style.visibility = 'hidden'
            }}
          />
        </div>
        <div className="min-w-0 flex-1">
          <Badge tone={item.wear_state === 'clean' ? 'sage' : 'amber'}>
            {zhWearState(item.wear_state)}
          </Badge>
          <p className="t-meta t-num mt-2">
            入库 {new Date(item.created_at).toLocaleDateString('zh-CN')}
          </p>
          <p className="t-meta t-num mt-0.5">
            累计穿过 {item.recent_wear_dates?.length ?? 0} 次
          </p>
        </div>
      </div>

      <div className="mt-4 rounded-xl px-3 py-1" style={{ background: 'var(--surface-2)' }}>
        <dl>
          <Field label="细分" value={zhSubcategory(item.subcategory, categoryZh)} />
          <Field label="颜色" value={zhColors(item.colors)} />
          <Field label="图案" value={zhText(item.pattern)} />
          <Field label="材质" value={zhText(item.material_guess)} />
          <Field label="正式度" value={item.formality == null ? '—' : `${item.formality} / 5`} />
          <Field label="季节" value={zhList(item.seasons)} />
          <Field label="风格" value={zhList(item.style_tags)} />
          <Field label="场合" value={zhList(item.occasion_tags)} />
          <Field label="保暖度" value={item.warmth == null ? '—' : `${item.warmth} / 5`} />
          <Field label="备注" value={item.user_note || '—'} />
          {Object.entries(item.attributes ?? {}).map(([key, value]) => (
            <Field key={key} label={DIMENSION_ZH[key] ?? key} value={zhText(value)} />
          ))}
        </dl>
      </div>

      {confirming ? (
        <div className="mt-4 rounded-xl p-3" style={{ background: '#fbf0ee' }}>
          <p className="font-semibold" style={{ fontSize: 14, color: 'var(--danger)' }}>
            确定把这件衣服从衣橱移除？
          </p>
          <p className="t-meta mt-1">相关的推荐与反馈记录会一并清除，不可撤销。</p>
          <div className="mt-3 flex gap-2">
            <Button variant="danger" disabled={busy} onClick={() => void remove()}>
              确认删除
            </Button>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void toggleWash()}>
            <Droplets size={14} />
            {item.wear_state === 'clean' ? '标记为待洗' : '洗干净了'}
          </Button>
          <Button variant="outline" onClick={onClose}>关闭</Button>
          <Button variant="danger" className="ml-auto" onClick={() => setConfirming(true)}>
            扔掉了 · 删除
          </Button>
        </div>
      )}
    </Dialog>
  )
}

// ---------------------------------------------------------------- 主组件
export function WardrobePanel({
  ownerId,
  scope,
  refreshKey,
  onToast,
  inTab = false,
}: Props) {
  const [items, setItems] = useState<WardrobeItem[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<WardrobeItem | null>(null)
  const [version, setVersion] = useState(0)
  const [open, setOpen] = useState(true)
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([])
  const [groupFilter, setGroupFilter] = useState<GroupFilter>('all')
  const [stateFilter, setStateFilter] = useState<'all' | 'clean' | 'dirty'>('all')

  async function load() {
    setLoading(true)
    try {
      setItems(await listGarments(ownerId || undefined, scope))
    } catch (error) {
      onToast(`读取衣橱失败：${errorMessage(error)}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerId, scope, refreshKey, version])

  const filtered = useMemo(
    () =>
      items.filter((item) => {
        if (!matchesGroup(item.category, groupFilter)) return false
        if (stateFilter === 'clean') return item.wear_state === 'clean' || item.wear_state === 'worn_once'
        if (stateFilter === 'dirty') return item.wear_state === 'dirty' || item.wear_state === 'laundry'
        return true
      }),
    [items, groupFilter, stateFilter],
  )

  const groups = useMemo(() => {
    const map = new Map<string, WardrobeItem[]>()
    for (const item of filtered) {
      const label = zhCategory(item.category)
      const list = map.get(label) ?? []
      list.push(item)
      map.set(label, list)
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
  }, [filtered])

  function toggleGroup(label: string) {
    setCollapsedGroups((current) =>
      current.includes(label)
        ? current.filter((item) => item !== label)
        : [...current, label],
    )
  }

  async function quickWash(item: WardrobeItem) {
    try {
      const next = item.wear_state === 'clean' ? 'dirty' : 'clean'
      await setWearState(item.id, next)
      onToast(next === 'dirty' ? '已标记为待洗' : '已标记为干净', 'success')
      setVersion((value) => value + 1)
    } catch (error) {
      onToast(errorMessage(error), 'error')
    }
  }

  async function quickDelete(item: WardrobeItem) {
    if (!window.confirm('确定从衣橱移除这件衣服？相关推荐与反馈会一并清除，不可撤销。')) return
    try {
      await deleteGarment(item.id)
      onToast('这件衣服已从衣橱移除', 'success')
      setVersion((value) => value + 1)
    } catch (error) {
      onToast(errorMessage(error), 'error')
    }
  }

  return (
    <section className="card-soft p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 items-center gap-2 text-left"
          aria-expanded={open}
        >
          <span
            style={{
              fontSize: 11,
              color: 'var(--ink-3)',
              transform: open ? 'rotate(90deg)' : 'none',
              display: 'inline-block',
            }}
          >
            ▶
          </span>
          <span className="t-title">我的衣橱</span>
          <Badge tone="sage" className="t-num">
            {loading ? '读取中…' : `${items.length} 件`}
          </Badge>
        </button>
        <Button variant="ghost" size="sm" onClick={() => void load()}>
          <RefreshCw size={13} /> 刷新
        </Button>
      </header>

      {open && (
        <>
          {/* U2：筛选栏 */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Segmented options={GROUP_OPTIONS} value={groupFilter} onChange={setGroupFilter} />
            <Segmented
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'clean', label: '干净' },
                { value: 'dirty', label: '待洗' },
              ]}
              value={stateFilter}
              onChange={setStateFilter}
            />
            <span className="t-caption ml-auto">
              {scope === 'shared' ? '全部用户' : '当前用户'} · 筛出 {filtered.length} 件
            </span>
          </div>

          {filtered.length === 0 ? (
            <p
              className="t-meta mt-4 rounded-xl p-6 text-center"
              style={{ background: 'var(--surface-2)' }}
            >
              没有符合条件的衣物。换个筛选条件，或在右下角「添加衣物」入库。
            </p>
          ) : (
            <div
              className={
                inTab
                  ? 'mt-4 space-y-6'
                  : 'mt-4 max-h-[calc(100vh-230px)] space-y-5 overflow-y-auto pr-1'
              }
            >
              {groups.map(([label, list]) => {
                const collapsed = collapsedGroups.includes(label)
                return (
                  <div key={label}>
                    <button
                      type="button"
                      onClick={() => toggleGroup(label)}
                      className="mb-3 flex w-full items-center gap-2 text-left"
                    >
                      <span className="t-caption">{collapsed ? '▶' : '▼'}</span>
                      <span className="t-subtitle">{label}</span>
                      <Badge className="t-num">{list.length}</Badge>
                      <span className="h-px flex-1" style={{ background: 'var(--line)' }} />
                    </button>

                    {!collapsed && (
                      <div className="masonry">
                        {list.map((item) => {
                          const dots = colorDots(item.colors)
                          return (
                            <div
                              key={item.id}
                              className="card-hover group relative cursor-pointer overflow-hidden rounded-xl border"
                              style={{
                                borderColor: 'var(--line)',
                                background: 'var(--surface)',
                              }}
                              onClick={() => setSelected(item)}
                            >
                              <div
                                className="w-full overflow-hidden"
                                style={{ background: 'var(--surface-2)' }}
                              >
                                <img
                                  src={imageUrl(item.image_url)}
                                  alt={label}
                                  loading="lazy"
                                  className="w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
                                  onError={(event) => {
                                    event.currentTarget.style.visibility = 'hidden'
                                  }}
                                />
                              </div>

                              <div className="px-2.5 py-2">
                                <div className="flex items-center justify-between gap-1.5">
                                  <p className="truncate font-medium" style={{ fontSize: 12 }}>
                                    {zhSubcategory(item.subcategory, label)}
                                  </p>
                                  <Badge tone={item.wear_state === 'clean' ? 'sage' : 'amber'}>
                                    {zhWearState(item.wear_state)}
                                  </Badge>
                                </div>
                                <p className="t-meta mt-0.5 truncate">{zhColors(item.colors)}</p>
                                <div className="mt-1.5 flex items-center gap-1">
                                  {dots.map((dot, index) => (
                                    <span
                                      key={`${dot.name}-${index}`}
                                      title={dot.name}
                                      className="h-3 w-3 rounded-full border"
                                      style={{ background: dot.hex, borderColor: 'var(--line)' }}
                                    />
                                  ))}
                                </div>
                              </div>

                              {/* 悬停浮出的快捷操作 */}
                              <div
                                className="absolute inset-x-0 bottom-0 flex gap-1 p-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                                style={{
                                  background:
                                    'linear-gradient(transparent, rgba(45,42,38,0.82))',
                                }}
                              >
                                <button
                                  type="button"
                                  title={item.wear_state === 'clean' ? '标记为待洗' : '洗干净了'}
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    void quickWash(item)
                                  }}
                                  className="flex flex-1 items-center justify-center gap-1 rounded-lg bg-white/90 py-1.5 text-[var(--ink)]"
                                  style={{ fontSize: 11 }}
                                >
                                  <Droplets size={13} /> 待洗
                                </button>
                                <button
                                  type="button"
                                  title="删除"
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    void quickDelete(item)
                                  }}
                                  className="flex items-center justify-center rounded-lg bg-white/90 px-2.5 text-[var(--danger)]"
                                >
                                  <Trash2 size={13} />
                                </button>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {selected && (
        <DetailDialog
          item={selected}
          onClose={() => setSelected(null)}
          onDeleted={() => setVersion((value) => value + 1)}
          onChanged={() => setVersion((value) => value + 1)}
          onToast={onToast}
        />
      )}
    </section>
  )
}
