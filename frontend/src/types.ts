/** M6 前端共享类型。字段以 M6.md / M2 / M3 契约为准。 */

export type WardrobeScope = 'mine' | 'shared' | 'all'

export type Scene =
  | 'work'
  | 'date'
  | 'weekend'
  | 'travel'
  | 'formal-event'
  | 'free_text'

export type FeedbackSignal =
  | 'liked'
  | 'disliked'
  | 'worn'
  | 'not_worn'
  | 'regenerated'

export type WearState = 'clean' | 'worn_once' | 'dirty' | 'laundry'

export interface User {
  id: string
  display_name: string
  created_at?: string
  settings?: Record<string, unknown>
}

/** M2 的入库响应。 */
export interface GarmentCreateResult {
  garment_id: string
  status: 'auto_accepted' | 'needs_category_confirmation'
  extracted: Record<string, unknown>
  confirmation_options?: string[] | null
}

export interface RecommendationItem {
  garment_id: string
  category?: string | null
  slot?: string | null
}

export interface Recommendation {
  style_direction: string
  items: RecommendationItem[]
  reasoning: string
  match_score: number
}

/** U1 的推荐响应：只有全局约束与一个自由组合候选池，没有槽位。 */
export interface RecommendResponse {
  session_id: string
  /** 候选池的中文说明（筛选放宽、品类偏单一等）。 */
  pool_notes?: string[]
  weather: {
    temperature?: number | null
    temperature_max?: number | null
    temperature_min?: number | null
    city?: string | null
    condition?: string | null
  }
  layering_plan: {
    layering_hint?: string
    style_direction?: string
    avoid_notes?: string
    formality_range?: { min: number; max: number }
    warmth_range?: { min: number; max: number }
  }
  recommendations: Recommendation[]
}

export interface RecommendPayload {
  owner_id: string
  scene: Scene
  free_text?: string | null
  location?: { lat: number; lon: number } | { city: string } | null
  temperature_override?: number | null
  /** 01:30：温度覆盖填「0-8」这类区间时分别传上下限。 */
  temperature_override_min?: number | null
  temperature_override_max?: number | null
  wardrobe_scope: WardrobeScope
}

/**
 * M6.md 的 /feedback 契约：{session_id, outfit_index, signal, wear_state?, free_text?}
 * 注意：M1 的 FeedbackCreate 用的是 wear_state_after 且必带 owner_id。
 * 这里按 M6 文档发；后端实现时若不一致，只需改 client.ts 一处。
 */
export interface FeedbackPayload {
  session_id: string
  outfit_index: number
  signal: FeedbackSignal
  wear_state?: WearState
  free_text?: string
}

/**
 * /evaluate-purchase 与 /gap-report 的响应 M6.md 没有规定字段；
 * M4 已在后端定义（见项目根 README 第 16 节）。这里保持宽松读取
 * （全部可选），避免后端字段微调就要同步改前端类型。
 */
export interface PurchaseEvaluation {
  verdict?: string
  llm_verdict?: string
  reasoning?: string
  llm_reasoning?: string
  feasible_outfit_count?: number
  scene_coverage?: string[]
  duplicate_risk?: string
  similar_items?: SimilarGarment[]
  [key: string]: unknown
}

export interface SimilarGarment {
  garment_id: string
  image_url?: string
  category?: string
  subcategory?: string
}

export interface GapReport {
  summary?: string
  gaps?: string[]
  statistics?: Record<string, unknown>
  owner_id?: string
  wardrobe_scope?: string
  [key: string]: unknown
}

/** `GET /garments` 返回的单件衣物（「我的衣橱」列表 + 详情弹窗用）。 */
export interface WardrobeItem {
  id: string
  owner_id: string
  category: string
  subcategory: string | null
  colors: unknown
  pattern: string | null
  material_guess: string | null
  material_confidence: number | null
  formality: number | null
  seasons: string[]
  style_tags: string[]
  occasion_tags: string[]
  warmth: number | null
  attributes: Record<string, string>
  image_url: string
  wear_state: string
  status: string
  user_note: string | null
  recent_wear_dates: string[]
  last_worn_at: string | null
  created_at: string
  updated_at: string
}

/** 用户可设定的推荐 / 穿着策略，存在 users.settings 里。 */
export interface RecommendPolicy {
  rewear: 'anytime' | 'no_yesterday' | 'rest_2' | 'rest_3' | 'rest_7'
  prefer: 'balanced' | 'least_worn' | 'newest'
  outfit_count: number
  /** 上班/正装场合的正式度要求，只影响「上班」场景。 */
  dress_code: 'business' | 'smart_casual' | 'free'
  /** 区间筛完没有合适单品时：strict=留空 / moderate=逐级放宽 / relaxed=全放宽。 */
  pool_fallback: 'strict' | 'moderate' | 'relaxed'
  /** 体感偏好，各三档；只交给模型参考，不参与任何程序过滤。 */
  cold_sensitivity: 'normal' | 'mild' | 'strong'
  heat_sensitivity: 'normal' | 'mild' | 'strong'
}
