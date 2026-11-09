/**
 * U5：所有高级配置的唯一入口（顶栏只留一个齿轮）。
 *
 * 右侧抽屉里按用途分区，原来散落在顶栏的模型选择、用户管理、推荐策略、
 * 详细设置、请求日志都从这里进入；密钥状态与今日 Token 统计也收进来，
 * 不再以悬浮小字遮挡内容区。
 */
import { motion } from 'framer-motion'
import {
  KeyRound,
  Settings2,
  SlidersHorizontal,
  Users,
  X,
} from 'lucide-react'
import type { SettingsInfo, UsageInfo } from '../api/client'
import { RequestLogPanel } from './RequestLogPanel'
import { Badge, Button, Select } from './ui'
import { useLanguage, type Language } from '../lib/language'
import type { WardrobeScope } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  keyReady: boolean
  settings: SettingsInfo | null
  usage: UsageInfo | null
  scope: WardrobeScope
  onScopeChange: (scope: WardrobeScope) => void
  onOpenModels: () => void
  onOpenUsers: () => void
  onOpenPolicy: () => void
}

function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border p-4" style={{ borderColor: 'var(--line)' }}>
      <h3 className="t-subtitle" style={{ color: 'var(--ink)' }}>{title}</h3>
      {hint && <p className="t-meta mt-1">{hint}</p>}
      <div className="mt-3 space-y-2">{children}</div>
    </section>
  )
}

export function SettingsDrawer({
  open,
  onClose,
  keyReady,
  usage,
  scope,
  onScopeChange,
  onOpenModels,
  onOpenUsers,
  onOpenPolicy,
}: Props) {
  const { lang, setLang } = useLanguage()
  if (!open) return null

  return (
    <div className="fixed inset-0 z-[90]">
      <button
        type="button"
        aria-label="关闭设置"
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
      />
      <motion.aside
        initial={{ x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        className="absolute right-0 top-0 flex h-full w-full max-w-[420px] flex-col border-l shadow-2xl"
        style={{ background: 'var(--surface)', borderColor: 'var(--line)' }}
      >
        <header className="flex items-center justify-between border-b px-5 py-4"
          style={{ borderColor: 'var(--line)' }}>
          <div className="flex items-center gap-2.5">
            <Settings2 size={19} style={{ color: 'var(--sage-deep)' }} />
            <h2 className="t-title" style={{ color: 'var(--ink)' }}>设置</h2>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </Button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          <Section title="运行状态" hint="数据只保存在这台电脑">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={keyReady ? 'sage' : 'amber'}>
                {keyReady ? '密钥就绪' : '未填密钥'}
              </Badge>
              {!keyReady && (
                <Button variant="soft" size="sm" onClick={onOpenModels}>
                  <KeyRound size={14} /> 去填写
                </Button>
              )}
            </div>
            {usage && (
              <p className="t-body" style={{ color: 'var(--ink-2)' }}>
                今日已生成 {usage.outfit_count} 套搭配，消耗约 {usage.total_tokens} tokens
              </p>
            )}
          </Section>

          <Section title="模型与 API" hint="三个环节可以各选不同模型；Key 保存在本机配置文件">
            <Button variant="soft" className="w-full justify-start" onClick={onOpenModels}>
              <KeyRound size={15} /> 模型设置 / 测试连接
            </Button>
          </Section>

          <Section title="界面语言" hint="切换后会立即生效；搭配理由与衣物数据仍按后端返回显示">
            <Select
              value={lang}
              onChange={(event) => setLang(event.target.value as Language)}
              aria-label="界面语言"
              className="w-full"
            >
              <option value="zh">简体中文</option>
              <option value="en">English</option>
            </Select>
          </Section>

          <Section title="衣橱范围" hint="切换后所有页面立即生效">
            <Select
              value={scope}
              onChange={(event) => onScopeChange(event.target.value as WardrobeScope)}
              aria-label="衣橱范围"
              className="w-full"
            >
              <option value="mine">只看我的</option>
              <option value="shared">全部衣橱</option>
            </Select>
          </Section>

          <Section title="用户与偏好">
            <Button variant="ghost" className="w-full justify-start" onClick={onOpenUsers}>
              <Users size={15} /> 管理用户
            </Button>
            <Button variant="ghost" className="w-full justify-start" onClick={onOpenPolicy}>
              <SlidersHorizontal size={15} /> 推荐策略
            </Button>
          </Section>

          <Section title="诊断" hint="出问题时看每次请求的状态码、耗时和路径">
            <RequestLogPanel />
          </Section>

          <p className="t-caption px-1 leading-relaxed" style={{ color: 'var(--ink-3)' }}>
            关闭窗口后程序会先隐藏到系统托盘，保存完数据再退出，请稍候几秒。
          </p>
        </div>
      </motion.aside>
    </div>
  )
}
