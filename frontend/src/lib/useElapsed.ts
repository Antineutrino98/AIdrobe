/** 长请求计时：让「搭配中…」变成「已等待 42 秒」，用户能判断是死是活。 */
import { useEffect, useState } from 'react'
import { getLanguage } from './language'

/**
 * @param active 是否正在等待
 * @returns 已等待的秒数；不活跃时归零
 */
export function useElapsed(active: boolean): number {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    if (!active) {
      setSeconds(0)
      return
    }
    const startedAt = Date.now()
    setSeconds(0)
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [active])

  return seconds
}

/**
 * 把秒数变成一句完整的话，按界面语言整句输出。
 * 00:51：调用方一律直接用返回值，不要再拼「已等待 / 搭配中…」前缀，
 * 否则英文界面会出现「Elapsed Elapsed 10 秒」这种重复混合。
 */
export function describeWait(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (getLanguage() === 'en') {
    if (seconds < 15) return `Generating… elapsed ${seconds}s`
    if (seconds < 60) return `Generating… elapsed ${seconds}s, model is thinking`
    return `Generating… elapsed ${minutes}m ${rest}s, still working — please keep this window open`
  }
  if (seconds < 15) return `搭配中… 已等待 ${seconds} 秒`
  if (seconds < 60) return `搭配中… 已等待 ${seconds} 秒，模型正在思考`
  return `搭配中… 已等待 ${minutes} 分 ${rest} 秒，模型仍在生成，不要关闭页面`
}
