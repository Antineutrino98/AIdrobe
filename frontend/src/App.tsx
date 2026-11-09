/**
 * U5 主框架
 * --------
 * 顶栏：左侧 Logo，右侧「当前用户」+「设置」齿轮，中间留空；
 *       模型选择、用户管理、推荐策略、详细设置、请求日志全部收进设置抽屉。
 * 导航：左侧固定侧栏（桌面）/ 底部 Tab（窄屏），统一 4 个核心页面。
 * 内容：1200px 居中，所有状态信息正常排版，不再有绝对定位悬浮小字。
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  Plus,
  Settings,
  Shirt,
  ShoppingBag,
  Sparkles,
  Users,
} from 'lucide-react'
import {
  getSettings,
  getUsage,
  getUsers,
  migrateLegacyKeys,
  type SettingsInfo,
  type UsageInfo,
} from './api/client'
import { ModelSettingsDialog } from './components/ModelSettingsDialog'
import { PolicyDialog } from './components/PolicyDialog'
import { SettingsDrawer } from './components/SettingsDrawer'
import { TextContextMenu } from './components/TextContextMenu'
import { ToastHost, useToasts } from './components/Toast'
import { UserDialog } from './components/UserDialog'
import { WardrobePanel } from './components/WardrobePanel'
import { WelcomeWizard } from './components/WelcomeWizard'
import { LanguageProvider } from './lib/language'
import { Button, Dialog, Select } from './components/ui'
import {
  getStoredScope,
  getStoredUserId,
  hasSkippedWizard,
  setStoredScope,
  setStoredUserId,
  skipWizard,
} from './lib/storage'
import { BuyTab } from './tabs/BuyTab'
import { DashboardTab } from './tabs/DashboardTab'
import { GapTab } from './tabs/GapTab'
import { IngestTab } from './tabs/IngestTab'
import { WearTab } from './tabs/WearTab'
import type { User, WardrobeScope } from './types'

const TABS = [
  { key: 'home', icon: LayoutDashboard, label: '首页', hint: '今天穿什么、最近收了什么' },
  { key: 'wardrobe', icon: Shirt, label: '我的衣橱', hint: '浏览所有衣物，随时添加新衣服' },
  { key: 'wear', icon: Sparkles, label: '穿搭推荐', hint: '按天气和场合生成成套搭配' },
  { key: 'buy', icon: ShoppingBag, label: '购前决策', hint: '买之前先算一算，也能查衣橱缺口' },
] as const

type TabKey = (typeof TABS)[number]['key']

/** 与桌面 exe 的托盘图标同源：圆角方块 + 衣架，不再用「衣」字。 */
function BrandMark() {
  return (
    <svg width="34" height="34" viewBox="0 0 64 64" aria-hidden="true">
      <rect x="2" y="2" width="60" height="60" rx="15" fill="var(--sage-deep)" />
      <path d="M32 15 V21" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
      <path d="M32 20 L13 34 H51 Z" fill="#fff" />
      <rect x="14" y="34" width="36" height="18" rx="2" fill="#fff" />
    </svg>
  )
}

export default function App() {
  const { toasts, push, dismiss } = useToasts()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabKey>('home')
  const [userId, setUserId] = useState<string>(() => getStoredUserId() ?? '')
  const [scope, setScope] = useState<WardrobeScope>(() => {
    const stored = getStoredScope()
    return stored === 'all' ? 'shared' : stored
  })
  const [manualMode, setManualMode] = useState(false)
  const [excluded, setExcluded] = useState<string[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [userDialogOpen, setUserDialogOpen] = useState(false)
  const [policyDialogOpen, setPolicyDialogOpen] = useState(false)
  const [ingestDialogOpen, setIngestDialogOpen] = useState(false)
  const [modelVersion, setModelVersion] = useState(0)
  const [wardrobeVersion, setWardrobeVersion] = useState(0)
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [usage, setUsage] = useState<UsageInfo | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const usersErrorShown = useRef(false)

  const usersQuery = useQuery({ queryKey: ['users'], queryFn: getUsers, retry: 0 })
  const users: User[] = usersQuery.data ?? []
  void modelVersion
  const keyReady = settings?.has_valid_keys ?? false

  useEffect(() => {
    let cancelled = false
    void (async () => {
      await migrateLegacyKeys()
      try {
        const info = await getSettings()
        if (cancelled) return
        setSettings(info)
        if (!info.has_valid_keys && !info.has_garments && !hasSkippedWizard()) {
          setWizardOpen(true)
        }
      } catch {
        // 后端尚未就绪时静默。
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const refresh = () => {
      void getUsage().then(setUsage).catch(() => {})
    }
    refresh()
    const timer = window.setInterval(refresh, 15_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (usersQuery.isError && !usersErrorShown.current) {
      usersErrorShown.current = true
      setManualMode(true)
      push('无法读取用户列表，可先手动填入用户 ID', 'info')
    }
  }, [usersQuery.isError, push])

  useEffect(() => {
    if (!manualMode && !userId && users.length > 0) {
      setUserId(users[0].id)
      setStoredUserId(users[0].id)
    }
  }, [users, userId, manualMode])

  /** U4：老用户掉线时只拦截会调用模型的页面，首页和衣橱仍可浏览。 */
  function switchTab(next: TabKey) {
    if (settings && !settings.has_valid_keys && (next === 'wear' || next === 'buy')) {
      if (!settings.has_garments) {
        setWizardOpen(true)
        return
      }
      push('API Key 未配置或已失效，请前往设置更新', 'error')
      setSettingsOpen(true)
      return
    }
    setActiveTab(next)
  }

  function changeUser(next: string) {
    setUserId(next)
    setStoredUserId(next)
    setWardrobeVersion((value) => value + 1)
  }

  function changeScope(next: WardrobeScope) {
    setScope(next)
    setStoredScope(next)
    setWardrobeVersion((value) => value + 1)
  }

  function exclude(fingerprint: string) {
    setExcluded((current) =>
      current.includes(fingerprint) ? current : [...current, fingerprint],
    )
  }

  function refreshUsers() {
    void queryClient.invalidateQueries({ queryKey: ['users'] })
  }

  const activeTabMeta = TABS.find((tab) => tab.key === activeTab) ?? TABS[0]
  const tabProps = {
    onToast: push,
    onWardrobeChanged: () => setWardrobeVersion((value) => value + 1),
  }

  const panes: Record<TabKey, React.ReactNode> = {
    home: (
      <DashboardTab
        ownerId={userId}
        scope={scope}
        refreshKey={wardrobeVersion}
        settings={settings}
        usage={usage}
        onGoRecommend={() => switchTab('wear')}
        onGoWardrobe={() => switchTab('wardrobe')}
        onGoBuy={() => switchTab('buy')}
        onOpenSettings={() => setSettingsOpen(true)}
      />
    ),
    wardrobe: (
      <WardrobePanel
        ownerId={userId}
        scope={scope}
        refreshKey={wardrobeVersion}
        onToast={push}
        inTab
      />
    ),
    wear: (
      <WearTab
        ownerId={userId}
        scope={scope}
        excluded={excluded}
        onExclude={exclude}
        onToast={push}
      />
    ),
    buy: (
      <div className="space-y-8">
        <BuyTab ownerId={userId} onToast={push} />
        <div className="border-t pt-6" style={{ borderColor: 'var(--line)' }}>
          <GapTab ownerId={userId} scope={scope} onToast={push} />
        </div>
      </div>
    ),
  }

  return (
    <LanguageProvider>
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      {/* ============================================ 顶部横栏（已瘦身） */}
      <header
        className="sticky top-0 z-30 border-b backdrop-blur"
        style={{ background: 'rgba(255,253,249,0.94)', borderColor: 'var(--line)' }}
      >
        <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-5">
          <div className="flex shrink-0 items-center gap-2.5">
            <BrandMark />
            <h1 className="t-display" style={{ color: 'var(--ink)' }}>AIdrobe</h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <Users size={15} style={{ color: 'var(--ink-3)' }} />
            {manualMode ? (
              <input
                value={userId}
                onChange={(event) => changeUser(event.target.value)}
                placeholder="用户 ID"
                className="ui-field w-44"
                aria-label="当前用户"
              />
            ) : (
              <Select
                value={userId}
                onChange={(event) => changeUser(event.target.value)}
                className="w-36"
                aria-label="当前用户"
              >
                {users.length === 0 ? (
                  <option value={userId}>{userId || '暂无用户'}</option>
                ) : (
                  users.map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.display_name}
                    </option>
                  ))
                )}
              </Select>
            )}
            <Button
              variant="soft"
              size="sm"
              onClick={() => setSettingsOpen(true)}
              aria-label="打开设置"
            >
              <Settings size={17} />
            </Button>
          </div>
        </div>
      </header>

      {/* ============================================ 左侧固定侧边栏 */}
      <aside
        className="fixed bottom-0 left-0 top-16 z-20 hidden w-[248px] flex-col border-r px-4 py-6 xl:flex"
        style={{ background: 'var(--surface)', borderColor: 'var(--line)' }}
      >
        <p className="t-eyebrow mb-4 px-3">功能</p>
        <nav className="space-y-1.5">
          {TABS.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => switchTab(tab.key)}
                className="flex w-full items-center gap-3.5 rounded-xl px-3.5 py-4 text-left transition"
                style={{
                  fontSize: 17,
                  fontWeight: active ? 600 : 400,
                  background: active ? 'var(--sage-soft)' : 'transparent',
                  color: active ? 'var(--sage-deep)' : 'var(--ink-2)',
                }}
              >
                <Icon size={24} strokeWidth={active ? 2.2 : 1.8} />
                <span className="truncate">{tab.label}</span>
                {active && (
                  <span className="ml-auto h-1.5 w-1.5 rounded-full"
                    style={{ background: 'var(--sage-deep)' }} />
                )}
              </button>
            )
          })}
        </nav>

        <div className="mt-auto px-3 pb-2">
          <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
            <p className="t-caption">数据只存在这台电脑</p>
            <p className="t-meta mt-1">关闭窗口后会保存到托盘，几秒后安全退出。</p>
          </div>
        </div>
      </aside>

      {/* ============================================ 主内容 */}
      <main className="min-w-0 xl:pl-[248px]">
        <div className="mx-auto max-w-[1200px] px-5 pb-28 pt-6 xl:pb-10">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="t-display" style={{ color: 'var(--ink)' }}>
                {activeTabMeta.label}
              </h2>
              <p className="t-meta mt-1">{activeTabMeta.hint}</p>
            </div>
            <span className="t-meta" style={{ color: 'var(--ink-3)' }}>
              {scope === 'shared' ? '全部衣橱' : '当前用户'}
            </span>
          </div>

          {TABS.map((tab) => {
            const active = activeTab === tab.key
            return (
              <motion.div
                key={tab.key}
                animate={{ opacity: active ? 1 : 0, x: active ? 0 : 10 }}
                transition={{ duration: 0.22, ease: 'easeOut' }}
                style={{ display: active ? 'block' : 'none' }}
              >
                {panes[tab.key]}
              </motion.div>
            )
          })}
        </div>
      </main>

      {/* U5：添加衣物的悬浮按钮只在「我的衣橱」出现 */}
      {activeTab === 'wardrobe' && (
        <button
          type="button"
          onClick={() => setIngestDialogOpen(true)}
          className="fixed bottom-24 right-5 z-40 flex items-center gap-2 rounded-full px-5 py-3.5 text-white shadow-lg transition hover:scale-[1.03] xl:bottom-8"
          style={{ background: 'var(--sage-deep)', fontSize: 14, fontWeight: 600 }}
          aria-label="添加衣物"
        >
          <Plus size={18} /> 添加衣物
        </button>
      )}

      {/* ============================================ 窄屏底部导航 */}
      <nav
        className="fixed bottom-0 left-0 right-0 z-30 border-t px-2 py-2.5 backdrop-blur xl:hidden"
        style={{ background: 'rgba(255,253,249,0.96)', borderColor: 'var(--line)' }}
      >
        <div className="mx-auto grid max-w-lg grid-cols-4 gap-1">
          {TABS.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => switchTab(tab.key)}
                className="flex flex-col items-center gap-1 rounded-xl py-2 font-medium transition"
                style={{
                  fontSize: 12,
                  background: active ? 'var(--sage-soft)' : 'transparent',
                  color: active ? 'var(--sage-deep)' : 'var(--ink-3)',
                }}
              >
                <Icon size={22} />
                {tab.label}
              </button>
            )
          })}
        </div>
      </nav>

      {/* ============================================ 弹窗与抽屉 */}
      <Dialog
        open={ingestDialogOpen}
        onOpenChange={setIngestDialogOpen}
        title="添加衣物"
        description="拖入图片或点击选择，一次可以选多张。"
        width="max-w-2xl"
      >
        <IngestTab ownerId={userId} compact {...tabProps} />
      </Dialog>

      {wizardOpen && (
        <WelcomeWizard
          onSetup={() => {
            setWizardOpen(false)
            setModelDialogOpen(true)
          }}
          onLater={() => {
            skipWizard()
            setWizardOpen(false)
          }}
        />
      )}

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        keyReady={keyReady}
        settings={settings}
        usage={usage}
        scope={scope}
        onScopeChange={changeScope}
        onOpenModels={() => {
          setSettingsOpen(false)
          setModelDialogOpen(true)
        }}
        onOpenUsers={() => {
          setSettingsOpen(false)
          setUserDialogOpen(true)
        }}
        onOpenPolicy={() => {
          setSettingsOpen(false)
          setPolicyDialogOpen(true)
        }}
      />

      <ModelSettingsDialog
        open={modelDialogOpen}
        onClose={() => setModelDialogOpen(false)}
        onChanged={() => {
          setModelVersion((value) => value + 1)
          void getSettings().then(setSettings).catch(() => {})
          void getUsage().then(setUsage).catch(() => {})
        }}
        onToast={push}
      />
      <UserDialog
        open={userDialogOpen}
        onClose={() => setUserDialogOpen(false)}
        users={users}
        currentUserId={userId}
        onUsersChanged={refreshUsers}
        onUserDeleted={(deletedId) => {
          if (userId === deletedId) {
            changeUser(users.find((item) => item.id !== deletedId)?.id ?? '')
          }
        }}
        onToast={push}
      />
      <PolicyDialog
        open={policyDialogOpen}
        onClose={() => setPolicyDialogOpen(false)}
        userId={userId}
        onToast={push}
      />
      <TextContextMenu />
      <ToastHost toasts={toasts} onDismiss={dismiss} />
    </div>
    </LanguageProvider>
  )
}
