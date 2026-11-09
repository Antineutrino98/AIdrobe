/** 可选大模型清单与价格。加模型 / 改价格只需要动这个文件。 */
export type Provider = 'qwen' | 'deepseek' | 'anthropic' | 'openai' | 'gemini'
export type RoleKey = 'vision' | 'planner' | 'stylist'

export interface ModelPreset {
  id: string
  label: string
  provider: Provider
  model: string
  baseUrl: string
  /** 每百万 token 的输入 / 输出价格，展示用；未公开价格的写 '—'。 */
  priceInput: string
  priceOutput: string
  note: string
}

export const QWEN_BASE_URL =
  'https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1'
export const DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
/** 以下三家都走各自的 OpenAI 兼容接口，Key 在设置页按服务商填写。 */
export const ANTHROPIC_BASE_URL = 'https://api.anthropic.com/v1'
export const OPENAI_BASE_URL = 'https://api.openai.com/v1'
export const GEMINI_BASE_URL =
  'https://generativelanguage.googleapis.com/v1beta/openai'

export const PROVIDER_LABELS: Record<Provider, string> = {
  qwen: 'Qwen / 百炼',
  deepseek: 'DeepSeek 官方',
  anthropic: 'Anthropic Claude',
  openai: 'OpenAI ChatGPT',
  gemini: 'Google Gemini',
}

export const PROVIDERS: Provider[] = [
  'qwen', 'deepseek', 'anthropic', 'openai', 'gemini',
]

const OFFICIAL_PRICE_NOTE = '价格以服务商官网为准；需 OpenAI 兼容接口'

export const MODEL_PRESETS: ModelPreset[] = [
  {
    id: 'qwen37',
    label: 'Qwen3.7 Flash',
    provider: 'qwen',
    model: 'qwen3.7-flash',
    baseUrl: QWEN_BASE_URL,
    priceInput: '￥0.2',
    priceOutput: '￥0.8',
    note: '最便宜，适合入库识别与品类判断',
  },
  {
    id: 'qwen38',
    label: 'Qwen3.8 Flash',
    provider: 'qwen',
    model: 'qwen3.8-flash',
    baseUrl: QWEN_BASE_URL,
    priceInput: '￥0.8',
    priceOutput: '￥2.7',
    note: '通用，可看图',
  },
  {
    id: 'qwen38omni',
    label: 'Qwen3.8 Omni Flash',
    provider: 'qwen',
    model: 'qwen3.8-omni-flash',
    baseUrl: QWEN_BASE_URL,
    priceInput: '￥0.8',
    priceOutput: '￥2.7',
    note: '全模态输入、仅文本输出',
  },
  {
    id: 'ds-qwen',
    label: 'DeepSeek V4.1 Flash（百炼）',
    provider: 'qwen',
    model: 'deepseek-v4.1-flash',
    baseUrl: QWEN_BASE_URL,
    priceInput: '￥1',
    priceOutput: '￥4',
    note: '用 Qwen 密钥，可看图',
  },
  {
    id: 'ds-official',
    label: 'DeepSeek V4.1 Flash（官方）',
    provider: 'deepseek',
    model: 'deepseek-flash',
    baseUrl: DEEPSEEK_BASE_URL,
    priceInput: '￥1',
    priceOutput: '￥4',
    note: '需要单独一套 DeepSeek 密钥，可看图',
  },

  // ---------------------------------------------------------- Anthropic Claude
  {
    id: 'claude-sonnet-45',
    label: 'Claude Sonnet 4.5',
    provider: 'anthropic',
    model: 'claude-sonnet-4-5',
    baseUrl: ANTHROPIC_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
  {
    id: 'claude-haiku-45',
    label: 'Claude Haiku 4.5',
    provider: 'anthropic',
    model: 'claude-haiku-4-5',
    baseUrl: ANTHROPIC_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
  {
    id: 'claude-opus-41',
    label: 'Claude Opus 4.1',
    provider: 'anthropic',
    model: 'claude-opus-4-1',
    baseUrl: ANTHROPIC_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },

  // ----------------------------------------------------------- OpenAI ChatGPT
  {
    id: 'gpt-4o',
    label: 'GPT-4o',
    provider: 'openai',
    model: 'gpt-4o',
    baseUrl: OPENAI_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
  {
    id: 'gpt-4o-mini',
    label: 'GPT-4o mini',
    provider: 'openai',
    model: 'gpt-4o-mini',
    baseUrl: OPENAI_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
  {
    id: 'gpt-41',
    label: 'GPT-4.1',
    provider: 'openai',
    model: 'gpt-4.1',
    baseUrl: OPENAI_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },

  // ------------------------------------------------------------ Google Gemini
  {
    id: 'gemini-25-flash',
    label: 'Gemini 2.5 Flash',
    provider: 'gemini',
    model: 'gemini-2.5-flash',
    baseUrl: GEMINI_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
  {
    id: 'gemini-25-pro',
    label: 'Gemini 2.5 Pro',
    provider: 'gemini',
    model: 'gemini-2.5-pro',
    baseUrl: GEMINI_BASE_URL,
    priceInput: '—',
    priceOutput: '—',
    note: OFFICIAL_PRICE_NOTE,
  },
]

export const ROLE_LABELS: Record<RoleKey, string> = {
  vision: '入库识别 / 品类判断',
  planner: '文本规划 / 查重 / 偏好',
  stylist: '搭配决策（看图）',
}

/** 只是默认值，用户在「模型设置」里可以任意改。 */
export const ROLE_DEFAULTS: Record<RoleKey, string> = {
  vision: 'qwen37',
  planner: 'qwen38',
  stylist: 'qwen38',
}

export const ROLE_KEYS: RoleKey[] = ['vision', 'planner', 'stylist']

export function findModel(id: string | null | undefined): ModelPreset {
  return MODEL_PRESETS.find((item) => item.id === id) ?? MODEL_PRESETS[0]
}

export function priceLabel(model: ModelPreset): string {
  if (model.priceInput === '—' && model.priceOutput === '—') {
    return '价格以服务商官网为准'
  }
  return `输入 ${model.priceInput} / 输出 ${model.priceOutput}（每百万 token）`
}
