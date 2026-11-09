import { KeyRound, Sparkles } from 'lucide-react'
import { Button } from './ui'

interface Props {
  /** 打开模型设置，去填 API Key。 */
  onSetup: () => void
  /** 写 localStorage 记录并关闭，下次启动不再强制弹。 */
  onLater: () => void
}

/**
 * U4 模块二：新用户欢迎向导。
 *
 * 只在「没有有效 Key 且衣橱为空」时全屏弹出；老用户掉线（有衣服但没 Key）
 * 走的是 App 里的动作拦截，不弹这个。
 */
export function WelcomeWizard({ onSetup, onLater }: Props) {
  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-900/55 px-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-7 text-center shadow-2xl">
        <div
          className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl"
          style={{ background: 'var(--accent-soft, #eef2ff)' }}
        >
          <Sparkles size={26} />
        </div>

        <h2 className="t-display mt-5 text-slate-900">欢迎使用 AIdrobe</h2>
        <p className="t-body mt-3 leading-relaxed text-slate-600">
          先把衣服拍照收进衣橱，程序会按天气和场合给你搭配建议。
          识别、搭配和购前判断需要调用 AI 模型，请先填好 API Key。
        </p>
        <p className="t-meta mt-3 text-slate-500">
          Key 保存在本机的配置文件里，界面只显示前后 4 位。
        </p>

        <div className="mt-6 space-y-2.5">
          <Button className="w-full" onClick={onSetup}>
            <KeyRound size={16} /> 去填写 API
          </Button>
          <Button variant="ghost" className="w-full" onClick={onLater}>
            稍后配置，先看看
          </Button>
        </div>
      </div>
    </div>
  )
}
