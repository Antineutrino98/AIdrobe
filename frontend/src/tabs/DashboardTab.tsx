/**
 * U5：首页 / 仪表盘。
 *
 * 顶栏原本悬浮的「未填密钥 / 今日统计」小字移到这里，作为正常排版的状态卡，
 * 不再绝对定位遮挡内容。首页只做概览与导航，具体功能仍在各自页面。
 */
import { useQuery } from '@tanstack/react-query'
import {
  ArrowRight,
  KeyRound,
  Shirt,
  ShoppingBag,
  Sparkles,
} from 'lucide-react'
import { imageUrl, listGarments } from '../api/client'
import type { SettingsInfo, UsageInfo } from '../api/client'
import { zhCategory, zhSubcategory } from '../lib/i18n'
import { getLanguage } from '../lib/language'
import type { WardrobeScope } from '../types'
import { Badge, Button } from '../components/ui'

interface Props {
  ownerId: string
  scope: WardrobeScope
  refreshKey: number
  settings: SettingsInfo | null
  usage: UsageInfo | null
  onGoRecommend: () => void
  onGoWardrobe: () => void
  onGoBuy: () => void
  onOpenSettings: () => void
}

export function DashboardTab({
  ownerId,
  scope,
  refreshKey,
  settings,
  usage,
  onGoRecommend,
  onGoWardrobe,
  onGoBuy,
  onOpenSettings,
}: Props) {
  const { data: items = [] } = useQuery({
    queryKey: ['dashboard-garments', ownerId, scope, refreshKey],
    queryFn: () => listGarments(ownerId, scope),
    retry: 0,
  })
  const recent = items.slice(0, 4)
  const keyReady = settings?.has_valid_keys ?? false
  const today = new Date().toLocaleDateString('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })

  return (
    <div className="space-y-5">
      {/* 今天穿什么 */}
      <section
        className="rounded-2xl border p-6"
        style={{ borderColor: 'var(--line)', background: 'var(--surface)' }}
      >
        <p className="t-eyebrow">{today}</p>
        <h3 className="t-display mt-2" style={{ color: 'var(--ink)' }}>
          今天穿什么？
        </h3>
        <p className="t-body mt-2" style={{ color: 'var(--ink-2)' }}>
          按今天的天气、场合和衣橱里现有的衣服，生成几套可以直接穿的搭配。
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button onClick={onGoRecommend}>
            <Sparkles size={16} /> 生成今日搭配
          </Button>
          <Button variant="ghost" onClick={onGoWardrobe}>
            <Shirt size={16} /> 先看看衣橱
          </Button>
        </div>
      </section>

      {/* 状态卡：原来是顶栏悬浮小字，现在正常排版 */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="card-soft p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="t-eyebrow">模型状态</span>
            <Badge tone={keyReady ? 'sage' : 'amber'}>
              {keyReady ? '密钥就绪' : '未填密钥'}
            </Badge>
          </div>
          <p className="t-body mt-2" style={{ color: 'var(--ink-2)' }}>
            {keyReady
              ? '识别、搭配和购前判断都可以正常调用。'
              : '还没配置 API Key，相关功能会提示你先去设置。'}
          </p>
          {!keyReady && (
            <Button variant="soft" size="sm" className="mt-3" onClick={onOpenSettings}>
              <KeyRound size={14} /> 打开设置
            </Button>
          )}
        </div>

        <div className="card-soft p-4">
          <span className="t-eyebrow">今日使用</span>
          <p className="t-title mt-2 t-num" style={{ color: 'var(--ink)' }}>
            {usage?.outfit_count ?? 0} 套
          </p>
          <p className="t-meta mt-1">
            今日已生成 {usage?.outfit_count ?? 0} 套搭配，
            消耗约 {(((usage?.total_tokens ?? 0) / 1_000_000)).toFixed(3)} M tokens
            <span className="t-meta ml-1">
              （{(usage?.total_tokens ?? 0).toLocaleString('zh-CN')} tokens）
            </span>
          </p>
        </div>
      </div>

      {/* 最近收进衣橱 */}
      <section className="card-soft p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="t-subtitle" style={{ color: 'var(--ink)' }}>最近收进衣橱</h3>
          <Button variant="ghost" size="sm" onClick={onGoWardrobe}>
            查看全部 <ArrowRight size={14} />
          </Button>
        </div>
        {recent.length === 0 ? (
          <p className="t-meta mt-3">
            衣橱还是空的。去「我的衣橱」点右下角「添加衣物」，拍张照就能入库。
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {recent.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={onGoWardrobe}
                className="overflow-hidden rounded-xl border text-left transition hover:opacity-85"
                style={{ borderColor: 'var(--line)' }}
              >
                <img
                  src={imageUrl(item.id)}
                  alt={zhCategory(item.category)}
                  className="aspect-[3/4] w-full object-cover"
                  style={{ background: 'var(--surface-2)' }}
                />
                <div className="px-2.5 py-2">
                  <p className="t-body truncate" style={{ color: 'var(--ink)' }}>
                    {getLanguage() === 'en'
                      ? item.subcategory || zhCategory(item.category)
                      : zhSubcategory(item.subcategory, zhCategory(item.category))}
                  </p>
                  <p className="t-caption">{zhCategory(item.category)}</p>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* 买之前先算一算 */}
      <section
        className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border p-4"
        style={{ borderColor: 'var(--line)', background: 'var(--surface)' }}
      >
        <div>
          <h3 className="t-subtitle" style={{ color: 'var(--ink)' }}>买之前先算一算</h3>
          <p className="t-meta mt-1">看看新衣服和现有衣橱能搭几套、有没有重复，也能查衣橱缺口。</p>
        </div>
        <Button variant="soft" onClick={onGoBuy}>
          <ShoppingBag size={16} /> 购前决策
        </Button>
      </section>
    </div>
  )
}
