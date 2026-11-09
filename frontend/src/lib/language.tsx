/**
 * 界面语言（中文 / English）。
 *
 * 全站中文文案上千处，直接改十几个组件风险太高。这里用「文本替换层」：
 * 切到英文时遍历 DOM 文本节点与 placeholder/title/aria-label，**只做整词 /
 * 整句模板匹配**（绝不子串替换）；MutationObserver 负责之后动态渲染出来的内容。
 *
 * 切回中文**不 reload**：翻译前把每个节点的原文记下来，切回时原样还原。
 * 以前会整页 reload，会把正在进行的搭配请求一起打断。
 *
 * 范围：界面文案。模型生成的搭配理由、衣物名称、品类等仍由后端返回中文。
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

export type Language = 'zh' | 'en'

const STORAGE_KEY = 'aidrobe.language'

/** 按长度降序使用，避免短词先替换把长词拆掉。 */
const ZH_EN: Record<string, string> = {
  '今天穿什么、最近收了什么': 'What to wear today, and what you just added',
  '浏览所有衣物，随时添加新衣服': 'Browse every garment and add new ones',
  '按天气和场合生成成套搭配': 'Generate complete outfits for the weather and occasion',
  '买之前先算一算，也能查衣橱缺口': 'Check before you buy, and find wardrobe gaps',
  '模型设置 / 测试连接': 'Model settings / Test connection',
  '数据只保存在这台电脑': 'Everything stays on this computer',
  '衣橱里已有类似单品': 'You already own something similar',
  '关闭窗口后程序会先隐藏到系统托盘，保存完数据再退出，请稍候几秒。':
    'When you close the window it hides in the tray, saves your data, then exits. Please wait a few seconds.',
  '这里上传的图片只用于这次评估，不会收进衣橱，也不会出现在穿搭推荐里。':
    'Images uploaded here are used for this evaluation only. They are not added to your wardrobe and will not appear in outfit suggestions.',
  '出问题时看每次请求的状态码、耗时和路径': 'Status, timing and path of every request',
  '切换后所有页面立即生效': 'Applies to every page immediately',
  '三个环节可以各选不同模型；Key 保存在本机配置文件':
    'Each step can use a different model. Keys are stored in a local file.',
  '界面语言': 'Interface language',
  '简体中文': 'Simplified Chinese',
  '英文': 'English',
  '请求日志': 'Request log',
  '运行状态': 'Status',
  '衣橱范围': 'Wardrobe scope',
  '用户与偏好': 'Users & preferences',
  '诊断': 'Diagnostics',
  '去填写': 'Add key',
  '今日使用': 'Today',
  '最近收进衣橱': 'Recently added',
  '查看全部': 'View all',
  '今天穿什么': "What to wear today",
  '查看详情': 'View details',
  '密钥就绪': 'API key ready',
  '未填密钥': 'No API key',
  '当前用户': 'Current user',
  '全部衣橱': 'All wardrobes',
  '只看我的': 'Mine only',
  '管理用户': 'Manage users',
  '推荐策略': 'Recommendation policy',
  '模型与 API': 'Models & API',
  '设置': 'Settings',
  '功能': 'Sections',
  '首页': 'Home',
  '我的衣橱': 'My wardrobe',
  '穿搭推荐': 'Outfits',
  '购前决策': 'Buy or not',
  '添加衣物': 'Add garment',
  '换一套': 'Another outfit',
  '今天穿了': 'Wore today',
  '不喜欢': 'Dislike',
  '喜欢': 'Like',
  '还干净（默认）': 'Still clean (default)',
  '该洗了': 'Needs washing',
  '取消': 'Cancel',
  '保存': 'Save',
  '关闭': 'Close',
  '恢复默认': 'Reset',
  '展开': 'Expand',
  '收起': 'Collapse',
  '给我搭配': 'Suggest outfits',
  '搭配中': 'Working on it',
  '造型师正在翻衣橱': 'Your stylist is going through the wardrobe',
  '已等待': 'Elapsed',
  '候选池说明': 'Candidate pool notes',
  '搭配提示': 'Styling note',
  '匹配度': 'Match',
  '件单品': 'pieces',
  '第 ': '#',
  ' 套': ' outfits',
  '套搭配': 'outfits',
  '消耗约': 'about',
  '今日已生成': 'Generated today',
  'tokens': 'tokens',
  '没有拿到搭配。可以换个场景、放宽衣橱范围，或稍后再试。':
    'No outfits this time. Try another occasion, widen the wardrobe scope, or try again later.',
  '把图片拖到这里，或点击选择': 'Drop images here, or click to choose',
  '正在识别衣物属性': 'Recognising garment attributes',
  '粘贴商品链接': 'Paste a product link',
  '识别': 'Recognise',
  '上传想买的图片': 'Upload the item you are considering',
  '评估': 'Evaluate',
  '评估结论': 'Verdict',
  '可搭配套数': 'Possible outfits',
  '可覆盖场景': 'Occasions covered',
  '重复风险较高': 'High duplicate risk',
  '衣橱缺口': 'Wardrobe gaps',
  '分析我的衣橱': 'Analyse my wardrobe',
  '统计概览': 'Statistics',
  '结论': 'Conclusion',
  '发现的缺口': 'Gaps found',
  '点上面的按钮开始分析。': 'Press the button above to start.',
  '穿着与推荐策略': 'Wearing & recommendation policy',
  '刚穿过的衣服': 'Recently worn garments',
  '上班穿成什么样': 'Dress code for work',
  '候选优先级': 'Candidate priority',
  '每次至少生成几套': 'Outfits per request',
  '很商务': 'Very business',
  '商务休闲': 'Smart casual',
  '无着装要求': 'No dress code',
  '均衡（不特别偏好）': 'Balanced',
  '优先没怎么穿过的': 'Least worn first',
  '优先新入库的': 'Newest first',
  '套数不足时直接返回已有结果，不会重复调用模型；这个数字会原样告诉搭配模型。':
    'If fewer outfits come back they are returned as-is; the model is never called twice. This number is passed to the model.',
  '还干净': 'Still clean',
  '无图片': 'No image',
  '暂无用户': 'No users',
  '请先在顶部选择用户，或手动填入用户 ID':
    'Choose a user at the top, or enter a user ID manually',
  '这次评估失败了': 'This evaluation failed',
  '这次分析失败了': 'This analysis failed',
  '操作失败，请重试': 'Something went wrong, please try again',
  '模型设置已保存，立即生效': 'Settings saved, effective immediately',
  '密钥': 'API key',
  '已保存的只显示前后 4 位': 'Saved keys show only the first and last 4 characters',
  '选用该服务商的模型时才需要': 'Only needed for models from this provider',
  '稍后配置，先看看': 'Set up later',
  '欢迎使用': 'Welcome',
  '成功': 'Success',
  '失败': 'Failed',
  '删除': 'Delete',
  '确定': 'Confirm',
  '搜索': 'Search',
  '暂无数据': 'No data',
  '加载中': 'Loading',

  // ---- 首页 / 概览
  '按今天的天气、场合和衣橱里现有的衣服，生成几套可以直接穿的搭配。':
    'Outfits for today, built from the weather, the occasion and what you already own.',
  '看看新衣服和现有衣橱能搭几套、有没有重复，也能查衣橱缺口。':
    'See how many outfits a new piece makes, check for duplicates, and find wardrobe gaps.',
  '识别、搭配和购前判断都可以正常调用。':
    'Recognition, styling and purchase checks are all working.',
  '还没配置 API Key，相关功能会提示你先去设置。':
    'No API key yet. The app will prompt you to set one up.',
  '识别、搭配和购前判断需要调用 AI 模型，请先填好 API Key。':
    'Recognition, styling and purchase checks need an AI model. Add an API key first.',
  '生成今日搭配': "Generate today's outfits",
  '先看看衣橱': 'Browse my wardrobe',
  '买之前先算一算': 'Buy or not',
  '模型状态': 'Model status',
  '打开设置': 'Open settings',

  // ---- 搭配表单
  '补充说明（可选）': 'Extra notes (optional)',
  '温度覆盖（℃，可选）': 'Temperature (°C, optional)',
  '位置 / 天气': 'Location / weather',
  '约会但不想太正式': 'A date, but not too formal',
  '场景': 'Occasion',
  '上班': 'Work',
  '约会': 'Date',
  '周末': 'Weekend',
  '出行': 'Travel',
  '正式场合': 'Formal event',
  '自己描述': 'Describe it myself',
  '不填': 'Skip',
  '城市': 'City',
  '这次请求失败了': 'This request failed',
  '这套没有单品信息': 'No item details for this outfit',
  '未命名风格': 'Unnamed style',

  // ---- 衣橱
  '全部状态': 'All states',
  '全部用户': 'All users',
  '全部': 'All',
  '上装': 'Tops',
  '下装': 'Bottoms',
  '裙装': 'Dresses',
  '外套': 'Outerwear',
  '鞋': 'Shoes',
  '干净': 'Clean',
  '待洗': 'To wash',
  '刷新': 'Refresh',
  '筛出': 'showing',
  '干净可穿': 'Ready to wear',
  '已标记为干净': 'Marked as clean',
  '标记为待洗': 'Mark for washing',
  '洗干净了': 'Mark as clean',
  '确认删除': 'Confirm delete',
  '这件衣服已从衣橱移除': 'Removed from your wardrobe',
  '细分': 'Subcategory',
  '颜色': 'Colour',
  '图案': 'Pattern',
  '单品': 'Piece',
  '衣物': 'Garment',

  // ---- 品类 / 颜色（后端历史英文值在中文模式下的兜底，英文模式同样要统一）
  '半身裙/裙裤': 'Skirt',
  '大衣/风衣': 'Trench / coat',
  '运动鞋': 'Sneakers',
  '夹克': 'Jacket',
  '衬衫': 'Shirt',
  '米白': 'Off-white',
  '象牙白': 'Ivory',
  '米色': 'Beige',

  // ---- 购前 / 缺口
  '或者': 'or',
  '看看还缺哪些场景或品类，避免重复购买。':
    'See which occasions or categories are missing, so you avoid buying duplicates.',
  '请先粘贴商品链接': 'Paste a product link first',
  '设置 → 诊断」里的请求日志有每次请求的状态码和耗时。':
    'The request log in Settings → Diagnostics shows status and timing for every call.',

  // ---- 设置 / 模型
  '密钥无效': 'Invalid key',
  '调用失败': 'Call failed',
  '未测试': 'Not tested',
  '测试中…': 'Testing…',
  '请先填写 API Key': 'Please add an API key first',
  '密钥已保存到本机浏览器': 'Key saved on this device',
  '密钥已清除': 'Key cleared',
  '模型 API Key': 'Model API key',
  '清除': 'Clear',
  '无法读取用户列表，可先手动填入用户 ID':
    'Could not load users; you can enter an ID manually',
  '未配置或已失效，请前往设置更新': 'Missing or expired, please update it in Settings',
  '密钥没有该模型的权限': 'This key has no access to that model',
  '调用过于频繁或额度用尽': 'Rate limited or quota exhausted',
  '响应超时': 'Request timed out',
  '网络连接失败': 'Network connection failed',
  '后端连接': 'Backend connection',
  '不要在公共电脑使用。': 'Do not use this on a shared computer.',
  '数据只存在这台电脑': 'Everything stays on this computer',
  '关闭窗口后会保存到托盘，几秒后安全退出。':
    'Closing hides it in the tray; it exits safely a few seconds later.',
  '拖入图片或点击选择，一次可以选多张。':
    'Drop images or click to choose; you can select several.',

  // 数字与量词在 DOM 里常是两个文本节点，短词也必须命中。
  '件': ' items',

  // ---- 21:56 反馈：整句翻译，杜绝中英混杂
  '三个环节分别选模型，价格是每百万 token 的输入 / 输出。Key 保存在本机 .env，界面只显示前后 4 位。':
    'Pick a model for each step. Prices are per million input / output tokens. Keys live in the local .env file; the UI shows only the first and last 4 characters.',
  '在搭配卡片上点「今天穿了」后，这一套里的每件衣服都会记下今天的穿着日期。':
    'Tapping "Wore today" on an outfit card records today as a wear date for every garment in it.',
  '该洗了、洗衣中的衣服始终不进推荐，不需要设置；「穿过一次」不算脏，':
    'Items needing washing or in the laundry never enter recommendations — no setting needed. "Worn once" is not dirty,',
  '能不能接着穿由下面第一项决定。策略按用户分别保存。':
    'Whether they can be worn again is set by the first option below. Policies are saved per user.',
  '家庭成员各建一个用户名，衣橱数据互相独立。没有登录，切换即生效。':
    'Give each family member a name; wardrobes stay separate. No login — switching takes effect immediately.',
  '确定从衣橱移除这件衣服？相关推荐与反馈会一并清除，不可撤销。':
    'Remove this garment? Related recommendations and feedback are deleted too. This cannot be undone.',
  '没有符合条件的衣物。换个筛选条件，或在右下角「添加衣物」入库。':
    'No garments match. Try another filter, or add one with the button at the bottom right.',
  '先把衣服拍照收进衣橱，程序会按天气和场合给你搭配建议。':
    'Photograph your clothes into the wardrobe first, then the app suggests outfits for the weather and occasion.',
  '只保存在本机浏览器 localStorage，随请求发送给你自己的后端；':
    'Stored only in this browser and sent to your own backend with each request;',
  '切换后会立即生效；搭配理由与衣物数据仍按后端返回显示':
    'Applies immediately; outfit reasoning and garment data are shown as returned by the backend',
  '只检查当前三个角色真正用到的服务商，没选的模型不要求填 Key。':
    'Only providers used by the three current roles are checked; unselected models need no key.',
  '入库识别与搭配决策会真的传一张图；标记为「读图异常」就说明该模型读不了图，':
    'Recognition and styling really send an image; a "vision error" mark means that model cannot read images,',
  '只存在后端 .env，界面拿到的始终是脱敏值（带 *）。':
    'Stored only in the backend .env; the UI always receives a masked value (with *).',
  '脱敏值带 *，后端会跳过、保留原 Key；空串表示明确清除。':
    'Masked values contain * and are skipped, keeping the original key; an empty string clears it.',
  '后端只在当前请求的内存里使用，不落盘、不写日志。':
    'The backend uses it only in memory for that request; never written to disk or logs.',
  '粘贴密钥；已保存的只显示前后 4 位': 'Paste a key; saved keys show only the first and last 4 characters',
  '保存在本机的配置文件里，界面只显示前后 4 位。':
    'Stored in a local config file; the UI shows only the first and last 4 characters.',
  '推荐记录、反馈与偏好历史。此操作不可撤销。':
    'Recommendation history, feedback and preference history. This cannot be undone.',
  '相关的推荐与反馈记录会一并清除，不可撤销。':
    'Related recommendations and feedback are deleted too. This cannot be undone.',
  '只影响「上班」这个场合，约会、周末不受影响。':
    'Only affects the Work occasion; dates and weekends are unaffected.',
  '已标记为待洗，推荐里不会再出现': 'Marked for washing; it will no longer appear in recommendations',
  '还没有用户，先在上面添加一个。': 'No users yet. Add one above.',
  '随时可再穿（刚穿过、没洗也能接着穿）': 'Wear again anytime (even just worn, unwashed)',
  '昨天或今天穿过的，今天不再推荐': 'Not again today if worn yesterday or today',
  '可以稍微放松，针织和干净运动鞋都行': 'A bit more relaxed: knits and clean sneakers are fine',
  '怎么舒服怎么穿，不刻意提高正式度': 'Dress for comfort, no need to look formal',
  '穿过后至少隔 2 天': 'At least 2 days between wears',
  '穿过后至少隔 3 天': 'At least 3 days between wears',
  '穿过后至少隔 7 天': 'At least 7 days between wears',
  '新用户名，例如：妈妈': 'New name, e.g. Mum',
  '（每百万 token）': 'per million tokens',

  // ---- 21:56 反馈：复合标签整词翻译，避免被短词切碎
  '模型设置': 'Model settings',
  '用户管理': 'User management',
  '入库识别 / 品类判断': 'Recognition / category check',
  '文本规划 / 查重 / 偏好': 'Planning / duplicate check / preferences',
  '搭配决策（看图）': 'Styling (vision)',
  '入库识别': 'Recognition',
  '品类判断': 'Category check',
  '文本规划': 'Text planning',
  '搭配决策': 'Styling',
  '改名': 'Rename',
  '添加': 'Add',
  '当前': 'Current',
  '输入': 'Input',
  '输出': 'Output',
  '可看图': 'supports images',
  '查重': 'Duplicate check',
  '偏好': 'Preferences',

  // ---- 00:51 反馈：整词命中，杜绝任何形式的子串切碎
  '输入城市名，自动查询当地温度': 'Enter a city to look up the local temperature',
  '支持一次多张，自动压缩': 'Multiple images at once, compressed automatically',
  '粘贴商品链接 https://…': 'Paste a product link https://…',
  '取消本次搭配': 'Cancel this request',
  '取消本次评估': 'Cancel this evaluation',
  '取消本次分析': 'Cancel this analysis',
  '已取消本次搭配': 'Recommendation cancelled',
  '已取消本次评估': 'Evaluation cancelled',
  '已取消本次分析': 'Analysis cancelled',
  '已记录：不喜欢，之后不再推荐这套': 'Noted: this outfit will not be suggested again',
  '造型师正在翻衣橱…': 'Your stylist is going through the wardrobe…',
  'AI 正在结合你的衣橱进行深度评估…': 'Evaluating this piece against your wardrobe…',
  '读取接口': 'GET request',
  '提交推荐搭配': 'Submit recommendation',
  '在库衣物总数': 'Garments in wardrobe',
  '统计天数': 'Days analysed',
  '参考历史次数': 'Sessions referenced',
  '各品类数量': 'Items by category',
  '场合表现': 'Occasion performance',
  '搭配不足的次数': 'Requests short of outfits',
  '搭配不足比例': 'Short ratio',
  '平均可用套数': 'Average outfits',
  '材质': 'Material',
  '正式度': 'Formality',
  '季节': 'Seasons',
  '风格': 'Style',
  '场合': 'Occasions',
  '保暖度': 'Warmth',
  '备注': 'Note',
  '长度': 'Length',
  '闭合方式': 'Closure',
  '纯色': 'Solid',
  '春、秋、冬': 'Spring, Autumn, Winter',
  '长款': 'Long',
  '纽扣门襟': 'Button placket',
  '经典、Smart casual、极简': 'Classic, Smart casual, Minimal',
  '通勤、Date、旅行': 'Commute, Date, Travel',
  '温度未知': 'Temperature unknown',
  '当地': 'Local',
  '这套没有给出理由': 'No reasoning given',
  '（这套没有给出理由）': '(No reasoning given)',

  // ---- 01:56：三档候选策略 + 怕冷 / 怕热各三档
  '库里没有符合要求的单品时': 'When no item meets the criteria',
  '严格：宁可少给或留空': 'Strict: fewer outfits or none',
  '适中：条件太窄时逐级放宽（默认）': 'Moderate: widen gradually when the range is too narrow (default)',
  '勉强推荐：放宽条件凑几套': 'Relaxed: widen fully to produce outfits',
  '正式度 / 保暖度不符合的单品一律不用；实在凑不出就返回 0 套。':
    'Items outside the formality / warmth ranges are excluded; if nothing works, return 0 outfits.',
  '先按区间筛；候选太少就把区间逐级外扩一级，尽量给出搭配。':
    'Filter by the ranges first; widen them one level at a time only when too few candidates remain.',
  '区间筛完不够时放宽到全部可穿单品，尽量给出搭配。':
    'When the ranges leave too few items, fall back to all wearable garments.',
  '偏怕冷': 'Sensitive to cold',
  '偏怕热': 'Sensitive to heat',
  '常规（按正常厚度）': 'Normal (standard thickness)',
  '有点怕冷（略偏厚）': 'A little cold-sensitive (slightly warmer)',
  '很怕冷（明显偏厚、更保暖）': 'Very cold-sensitive (noticeably warmer)',
  '有点怕热（略偏薄）': 'A little heat-sensitive (slightly lighter)',
  '很怕热（明显偏薄、更透气）': 'Very heat-sensitive (noticeably lighter)',
}

const MONTHS_EN = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]
const WEEKDAYS_EN: Record<string, string> = {
  一: 'Mon', 二: 'Tue', 三: 'Wed', 四: 'Thu',
  五: 'Fri', 六: 'Sat', 日: 'Sun', 天: 'Sun',
}

/**
 * 00:51 用户反馈后彻底换方案：**不再做任何子串替换**。
 *
 * 之前 `鞋→Shoes` `保存→Save` `设置→Settings` 这类短词会把长句切碎成
 * 「模型Settings」「每 items衣服」「策略按用户分别Save」，连中文界面都被污染。
 * 现在只认两种命中：
 *   1) 整个文本节点 trim 后**完全等于**字典 key（静态标签、句子、placeholder）；
 *   2) 整个文本节点匹配一条**整句模板**（含动态数字的组合文案）。
 * 命中不了就原样返回：宁可保留整句中文，也绝不产出中英夹杂的怪话。
 * 模型生成的自由文本由后端按 X-UI-Language 输出对应语言，前端一概不碰。
 */
const TEMPLATES: [RegExp, (match: RegExpMatchArray) => string][] = [
  // 首页日期：toLocaleDateString('zh-CN') → 「11月9日星期一」
  [/^(\d{1,2})月(\d{1,2})日星期([一二三四五六日天])$/, (m) =>
    `${WEEKDAYS_EN[m[3]] ?? m[3]}, ${MONTHS_EN[Number(m[1]) - 1] ?? m[1]} ${m[2]}`],
  // 今日使用（含动态套数与 token 数）
  [/^今日已生成\s*(\d+)\s*套搭配，消耗约\s*([\d.]+)\s*M tokens$/, (m) =>
    `Generated today ${m[1]} outfits, about ${m[2]} M tokens`],
  [/^(\d+)\s*套$/, (m) => `${m[1]} outfits`],
  // 搭配卡片
  [/^匹配度\s*(\d+)\/10\s*·\s*(\d+)\s*件单品$/, (m) => `Match ${m[1]}/10 · ${m[2]} pieces`],
  [/^第\s*(\d+)\s*套$/, (m) => `Outfit #${m[1]}`],
  [/^搭配\s*(\d+)$/, (m) => `Outfit ${m[1]}`],
  [/^已等待\s*(.+)$/, (m) => `Elapsed ${m[1]}`],
  // 请求日志
  [/^Request log（(\d+)\s*条）$/, (m) => `Request log (${m[1]})`],
  [/^(\d{2}:\d{2}:\d{2})\s*·\s*(\d+)\s*毫秒$/, (m) => `${m[1]} · ${m[2]} ms`],
  // 衣物详情
  [/^入库\s*(\d{4}\/\d{1,2}\/\d{1,2})$/, (m) => `Added ${m[1]}`],
  [/^累计穿过\s*(\d+)\s*次$/, (m) => `Worn ${m[1]} times`],
  // 缺口统计里的场合行
  [/^(\w+)\s*请求次数\s*(\d+)\s*·\s*搭配不足的次数\s*(\d+)\s*·\s*搭配不足比例\s*([\d.]+)\s*·\s*平均可用套数\s*([\d.]+)$/,
    (m) => `${m[1]}  requests ${m[2]} · short of outfits ${m[3]} · short ratio ${m[4]} · average outfits ${m[5]}`],
]

export function translateText(text: string): string {
  const trimmed = text.trim()
  const exact = ZH_EN[trimmed]
  if (exact !== undefined) return text.replace(trimmed, exact)
  for (const [pattern, render] of TEMPLATES) {
    const match = trimmed.match(pattern)
    if (match) return text.replace(trimmed, render(match))
  }
  return text
}

export function getLanguage(): Language {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'en' ? 'en' : 'zh'
  } catch {
    return 'zh'
  }
}

export function setLanguage(lang: Language): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    // 隐私模式下不可写，忽略
  }
}

/** 记住被翻译前的原文：切回中文时原样还原，不用刷新整页。 */
const textOriginals = new WeakMap<Text, string>()
const attrOriginals = new WeakMap<Element, Record<string, string>>()

function translateDom(): void {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  let node: Node | null
  const pending: [Text, string][] = []
  while ((node = walker.nextNode())) {
    const value = node.nodeValue
    if (!value || !/[\u4e00-\u9fa5]/.test(value)) continue
    const translated = translateText(value)
    if (translated === value) continue
    const textNode = node as Text
    // 只在第一次翻译时记原文，反复调用不会把英文当成中文存进去。
    if (!textOriginals.has(textNode)) textOriginals.set(textNode, value)
    pending.push([textNode, translated])
  }
  for (const [target, value] of pending) target.nodeValue = value

  document.querySelectorAll<HTMLElement>('[placeholder],[title],[aria-label]').forEach((el) => {
    for (const attr of ['placeholder', 'title', 'aria-label']) {
      const value = el.getAttribute(attr)
      if (!value || !/[\u4e00-\u9fa5]/.test(value)) continue
      const translated = translateText(value)
      if (translated === value) continue
      const saved = attrOriginals.get(el) ?? {}
      if (!(attr in saved)) saved[attr] = value
      attrOriginals.set(el, saved)
      el.setAttribute(attr, translated)
    }
  })
}

function restoreDom(): void {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  let node: Node | null
  while ((node = walker.nextNode())) {
    const original = textOriginals.get(node as Text)
    // 没记录在案的节点是 React 之后新建的，本来就是源码里的中文，不用动。
    if (original !== undefined) (node as Text).nodeValue = original
  }
  document.querySelectorAll<HTMLElement>('[placeholder],[title],[aria-label]').forEach((el) => {
    const saved = attrOriginals.get(el)
    if (!saved) return
    for (const [attr, value] of Object.entries(saved)) el.setAttribute(attr, value)
  })
}

interface LanguageValue {
  lang: Language
  setLang: (lang: Language) => void
}

const LanguageContext = createContext<LanguageValue>({
  lang: 'zh',
  setLang: () => {},
})

export function useLanguage(): LanguageValue {
  return useContext(LanguageContext)
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Language>(getLanguage)

  useEffect(() => {
    // cleanup 先于新 effect 执行：切回中文时 observer 已经断开，
    // 还原 DOM 不会再被重新翻译成英文。
    if (lang === 'zh') {
      restoreDom()
      return
    }
    translateDom()
    // React 之后渲染出来的中文文本也要翻译；translateText 对英文是幂等的，
    // 所以第二次回调不会再改 DOM，不会形成死循环。
    const observer = new MutationObserver(translateDom)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    return () => observer.disconnect()
  }, [lang])

  function setLang(next: Language) {
    if (next === lang) return
    setLanguage(next)
    // 不再 reload：整页刷新会掐断正在进行的搭配请求。
    setLangState(next)
    // 00:51：上一次请求生成的理由/结论用的是当时的语言，切语言后
    // 留着就会在中文界面显示英文。通知各页面清掉旧结果（busy 时跳过）。
    window.dispatchEvent(new CustomEvent('aidrobe:language-change', { detail: next }))
  }

  return (
    <LanguageContext.Provider value={{ lang, setLang }}>
      {children}
    </LanguageContext.Provider>
  )
}
