/** 中文显示名：品类与维度直接由后端契约生成，避免两处手抄不一致。 */

export const CATEGORY_ZH: Record<string, string> = {
  'top': '上装（通用）',
  't_shirt': 'T恤',
  'shirt': '衬衫',
  'sweater': '毛衣/针织衫',
  'hoodie': '卫衣',
  'vest': '马甲/背心',
  'tank_top': '吊带/汗背心',
  'base_layer': '保暖内衣/打底',
  'bottom': '下装（通用）',
  'trousers': '西裤/休闲裤',
  'jeans': '牛仔裤',
  'shorts': '短裤',
  'skirt': '半身裙/裙裤',
  'dress': '连衣裙',
  'jumpsuit': '连体衣',
  'suit': '西服/套装',
  'robe': '袍/旗袍',
  'outerwear': '外套（通用）',
  'coat': '大衣/风衣',
  'jacket': '夹克',
  'blazer': '西装外套',
  'down_jacket': '羽绒服',
  'cape': '披风/斗篷',
  'raincoat': '雨衣',
  'activewear': '运动服',
  'swimwear': '泳装',
  'sleepwear': '家居服/睡衣',
  'underwear': '内衣',
  'socks': '袜',
  'traditional': '民族/传统服装',
  'ceremonial': '婚纱礼服',
  'uniform': '制服/职业装',
  'costume': '戏服/演出服',
  'shoes': '鞋（通用）',
  'sneakers': '运动鞋',
  'boots': '靴',
  'sandals': '凉鞋',
  'slippers': '拖鞋',
  'accessory': '配饰（通用）',
  'hat': '帽/头饰',
  'scarf': '围巾/披肩',
  'tie': '领带/领结',
  'gloves': '手套',
  'belt': '腰带',
  'bag': '箱包',
  'jewelry': '首饰',
  'eyewear': '眼镜',
  'other': '其他（收容类目）',
}

export const DIMENSION_ZH: Record<string, string> = {
  'fit_type': '版型',
  'length': '长度',
  'sleeve_length': '袖长',
  'neckline': '领型',
  'collar_style': '领口样式',
  'pant_leg_style': '裤型',
  'waist_rise': '腰线',
  'silhouette': '廓形',
  'closure': '闭合方式',
  'texture': '纹理',
}


export const WEAR_STATE_ZH: Record<string, string> = {
  clean: '干净可穿',
  worn_once: '穿过一次',
  dirty: '该洗了',
  laundry: '洗衣中',
}

export const VERDICT_ZH: Record<string, string> = {
  buy: '建议购买',
  recommended: '建议购买',
  think_again: '再考虑一下',
  skip: '不建议买',
  avoid: '不建议买',
  duplicate: '衣橱里已有类似款',
}

export const SIGNAL_ZH: Record<string, string> = {
  liked: '喜欢',
  disliked: '不喜欢',
  worn: '今天穿了',
  not_worn: '没穿',
  regenerated: '换一批',
}

/** 缺口统计里的英文字段名 → 中文标签。 */
export const GAP_STAT_ZH: Record<string, string> = {
  lookback_days: '统计天数',
  sessions_considered: '参考历史次数',
  owned_garment_count: '在库衣物总数',
  requests: '请求次数',
  weak_requests: '搭配不足的次数',
  weak_ratio: '搭配不足比例',
  avg_feasible_outfits: '平均可用套数',
  missing_slot_count: '缺件阈值',
  weak_scene_ratio: '弱势场景阈值',
  min_feasible_outfits: '最少可用套数',
  min_match_score: '最低匹配分',
  temperature_range: '温度区间',
  gaps: '发现的缺口',
  owned_by_category: '各品类数量',
  scene_stats: '场景统计',
  thresholds: '判定阈值',
  feasible_outfit_count_note: '数据说明',
}

export function zhCategory(value: unknown): string {
  const key = String(value ?? '')
  return CATEGORY_ZH[key] ?? key ?? '—'
}

export function zhWearState(value: unknown): string {
  const key = String(value ?? '')
  return WEAR_STATE_ZH[key] ?? key ?? '—'
}

export function zhVerdict(value: unknown): string {
  const key = String(value ?? '').toLowerCase()
  return VERDICT_ZH[key] ?? (String(value ?? '') || '—')
}

export function zhStatKey(key: string): string {
  return GAP_STAT_ZH[key] ?? key
}

/** 把颜色数组渲染成中文，如「藏青 / 白」。 */
export function zhColors(value: unknown): string {
  if (!Array.isArray(value)) return value == null ? '—' : zhText(value)
  const parts = value
    .map((item) => {
      if (typeof item === 'string') return zhText(item)
      if (item && typeof item === 'object') {
        const obj = item as Record<string, unknown>
        return zhText(obj.name ?? obj.zh ?? obj.color ?? obj.value ?? '')
      }
      return ''
    })
    // 「米白 / ivory」这种，只留中文那一段
    .filter((part) => part && part !== '—' && hasChinese(part))
  return parts.length > 0 ? parts.join(' / ') : '—'
}

export const WEATHER_ZH: Record<string, string> = {
  clear: '晴',
  'partly-cloudy': '局部多云',
  cloudy: '多云',
  fog: '雾',
  drizzle: '毛毛雨',
  rain: '雨',
  snow: '雪',
  showers: '阵雨',
  thunderstorm: '雷雨',
  unknown: '天气未知',
}

export function zhWeather(value: unknown): string {
  const key = String(value ?? '')
  return WEATHER_ZH[key] ?? key ?? '天气未知'
}


/* ---------------------------------------------------------------- 英文值兜底
 * 旧数据由早期英文 prompt 生成，界面上一片英文。新 prompt 已要求中文输出，
 * 这里把历史值、以及 attributes 里冻结的英文枚举统一翻成中文。
 */
export const VALUE_ZH: Record<string, string> = {
  a_line: 'A 字廓形', bootcut: '喇叭裤', button: '纽扣门襟', collar: '有领',
  crew: '圆领', crop: '短款', fitted: '合身', fuzzy: '绒面',
  h_line: 'H 型廓形', high: '高腰', hooded: '连帽', knit: '针织',
  lapel: '翻领', long: '长款', loose: '宽松', low: '低腰',
  maxi: '及踝长款', mid: '中腰', none: '无', notched: '缺口翻领',
  other: '其他', oversized: '宽松廓形', pullover: '套头', quilted: '绗缝',
  regular: '常规', ribbed: '罗纹', short: '短', skinny: '紧身',
  sleeveless: '无袖', slim: '修身', smooth: '平滑', spread: '宽角领',
  stand: '立领', straight: '直筒', tapered: '锥形', three_quarter: '七分袖',
  tie: '系带', turtleneck: '高领', v_neck: 'V 领', wide: '阔腿',
  x_line: 'X 型廓形', zip: '拉链',
}

export const COLOR_ZH: Record<string, string> = {
  'pale pink': '浅粉', cream: '米白', 'off-white': '米白', beige: '米色',
  black: '黑色', white: '白色', gray: '灰色', grey: '灰色', navy: '藏青',
  'navy blue': '藏青', blue: '蓝色', 'light blue': '浅蓝', 'dark blue': '深蓝',
  'denim blue': '牛仔蓝', red: '红色', green: '绿色', brown: '棕色',
  khaki: '卡其', yellow: '黄色', purple: '紫色', orange: '橙色',
  silver: '银色', gold: '金色', pink: '粉色', charcoal: '炭灰',
  burgundy: '酒红', olive: '橄榄绿', camel: '驼色', tan: '棕褐',
  'light gray': '浅灰', 'dark gray': '深灰', multicolor: '多色',
}

export const PATTERN_ZH: Record<string, string> = {
  stripe: '条纹', striped: '条纹', solid: '纯色', check: '格纹',
  plaid: '格纹', floral: '印花', dot: '波点', polka_dot: '波点',
  print: '印花', geometric: '几何纹', colorblock: '拼色', plain: '纯色',
}

export const MATERIAL_ZH: Record<string, string> = {
  cotton: '棉', wool: '羊毛', linen: '亚麻', polyester: '聚酯纤维',
  denim: '牛仔布', silk: '真丝', leather: '皮革', knit: '针织',
  cashmere: '羊绒', nylon: '尼龙', velvet: '丝绒', corduroy: '灯芯绒',
  fleece: '抓绒', chiffon: '雪纺', suede: '麂皮',
}

export const SEASON_ZH: Record<string, string> = {
  spring: '春', summer: '夏', autumn: '秋', winter: '冬', all: '四季',
}

export const TAG_ZH: Record<string, string> = {
  casual: '休闲', work: '通勤', date: '约会', weekend: '周末',
  travel: '旅行', minimal: '极简', streetwear: '街头',
  'smart-casual': '商务休闲', formal: '正式', sporty: '运动',
  vintage: '复古', preppy: '学院风', business: '商务', party: '聚会',
  feminine: '女性化', romantic: '浪漫', sweet: '甜美', sporty2: '运动',
  tulle: '薄纱', mesh: '网纱', waistband: '腰头', elastic: '弹力',
  pleated: '百褶', layered: '层次', sheer: '透视', glitter: '亮片',
  outdoor: '户外', lounge: '居家', elegant: '优雅', classic: '经典',
}

const PHRASE_ZH: Array<[RegExp, string]> = [
  [/tulle mesh with ribbed knit waistband/i, '薄纱网纱，罗纹针织腰头'],
  [/ribbed knit waistband/i, '罗纹针织腰头'],
  [/tulle mesh/i, '薄纱网纱'],
  [/ribbed knit/i, '罗纹针织'],
  [/cotton blend/i, '棉混纺'],
  [/wool blend/i, '羊毛混纺'],
  [/cashmere blend/i, '羊绒混纺'],
  [/faux leather/i, '人造革'],
]

function translatePhrase(text: string): string {
  for (const [pattern, zh] of PHRASE_ZH) {
    if (pattern.test(text)) return zh
  }
  return text
}

export function hasChinese(text: string): boolean {
  return /[\u4e00-\u9fa5]/.test(text)
}

function translatePart(part: string): string {
  const key = part.trim().toLowerCase()
  if (!key) return ''
  return (
    COLOR_ZH[key] ??
    PATTERN_ZH[key] ??
    MATERIAL_ZH[key] ??
    SEASON_ZH[key] ??
    TAG_ZH[key] ??
    VALUE_ZH[key] ??
    part.trim()
  )
}

/**
 * 把值翻成中文；支持 "条纹 / 黑色" 这类复合值。
 *
 * 如果某几段翻不出中文（例如 "米白 / ivory"），就把纯英文段丢掉；
 * 全是英文时原样返回，由调用方决定是否回退（见 zhSubcategory）。
 */
export function zhText(value: unknown): string {
  const raw = String(value ?? '').trim()
  if (!raw) return '—'
  const phrase = translatePhrase(raw)
  const parts = phrase
    .split(/\s*\/\s*|\s*,\s*|、/)
    .map(translatePart)
    .filter(Boolean)
  // 硬规则：界面上不允许出现英文。翻不出来的段一律丢掉。
  const chinese = parts.filter(hasChinese)
  return chinese.length > 0 ? chinese.join(' / ') : '—'
}

/** 细分：翻不出来（仍是英文）就回退到品类中文名，绝不显示英文。 */
export function zhSubcategory(value: unknown, fallback: string): string {
  const text = zhText(value)
  return hasChinese(text) ? text : fallback
}

/** 数组字段翻成中文顿号列表。 */
export function zhList(value: unknown): string {
  if (!Array.isArray(value)) return value == null ? '—' : zhText(value)
  const parts = value
    .map(zhText)
    .filter((item) => item && item !== '—' && hasChinese(item))
  return parts.length > 0 ? parts.join('、') : '—'
}
