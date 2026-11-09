/**
 * 推荐等待期的「陪聊」文案。
 *
 * 一次搭配要 1~2 分钟，纯前端按固定节奏逐条渐显几句闲话，把空白填上。
 * 刻意不断言具体天气（晴天/下雨交给后端天气接口），也不额外调用模型：
 * 不增加耗时、不花钱、不改变后端返回结构。
 */
import { getLanguage } from './language'

export interface ChatterContext {
  city?: string
  scene?: string
  temperature?: string
}

const SCENE_LINES: Record<string, string> = {
  work: '上班的话，正式度得拿捏住：太随意显得没精神，太端着又一天都难受。',
  date: '约会嘛，颜色别太杂，留一个让人记得住的细节就够了。',
  weekend: '周末就别太较劲了，舒服第一，能随手一穿出门最好。',
  travel: '出门在外，好走路、能叠穿、不占箱子，比什么都实在。',
  formal: '正式场合先把轮廓收拾干净，再谈花色。',
}

const SCENE_LINES_EN: Record<string, string> = {
  work: 'For work the dress code has to land right: too casual looks sloppy, too stiff feels awful all day.',
  date: 'For a date, keep the palette quiet and leave one detail worth remembering.',
  weekend: 'Weekends should be easy — comfort first, something you can just pull on and go.',
  travel: 'When travelling, walkable, layerable and easy to pack beat everything else.',
  formal: 'For a formal occasion, get the silhouette clean before thinking about pattern.',
}

/** 英文界面下陪聊也用英文；这里按语言整段切换，不做逐词替换。 */
function englishLines(ctx: ChatterContext): string[] {
  const city = ctx.city?.trim() || 'your area'
  const temp = ctx.temperature?.trim()
  return [
    'Right, let me look through the wardrobe and see what is wearable today.',
    `I am checking the weather in ${city} too — thickness has to match the temperature.`,
    temp
      ? `You said about ${temp}°, so I am picking within that range — not too heavy, not too thin.`
      : 'Morning-to-evening swings are deceptive; I will leave one layer you can take off.',
    SCENE_LINES_EN[ctx.scene ?? ''] ?? 'First, what is the day for? That decides the direction.',
    'I lean towards one neutral base with a bit of echo; too many loud pieces fight each other.',
    'Weight, length and fit have to stack — I cross out anything that looks good but cannot be worn.',
    'Both halves matter; tops alone will not get you out the door.',
    'Moving recently worn pieces back so the neglected ones get a turn.',
    'Nearly there — writing the reasoning for each outfit and double-checking the photos.',
    `Almost done. Hope your ${ctx.scene === 'work' ? 'workday' : ctx.scene === 'date' ? 'date' : 'day'} goes well.`,
    'One more moment, I will bring them all up together.',
    '(Still thinking…)',
  ]
}

export function buildChatterLines(ctx: ChatterContext): string[] {
  if (getLanguage() === 'en') return englishLines(ctx)
  const city = ctx.city?.trim() || '你那儿'
  const temp = ctx.temperature?.trim()
  return [
    '嗯…先把衣橱从头翻一遍，看看今天有哪些能穿的。',
    `${city}的天气我也在看着，先别急，厚薄得跟温度对上。`,
    temp
      ? `你说大概 ${temp}° 左右，那我按这个范围挑，不太厚也不太单薄。`
      : '温度最容易被早晚温差骗，得留一件随时能脱下来的外层。',
    SCENE_LINES[ctx.scene ?? ''] ?? '先想想今天要干嘛，再决定往哪个方向靠。',
    '颜色上我倾向一个中性底加一点呼应，几件都很花的话会互相打架。',
    '厚薄、长短、松紧得能叠在一起，好看但穿不住的组合我会直接划掉。',
    '上下都得顾到，光有上装可出不了门。',
    '再把最近穿过的挪到后面，让压箱底的那几件也有机会。',
    '快好了，正在给每套写理由，顺便核对有没有拿错图片。',
    `差不多啦，祝你今天${ctx.scene === 'work' ? '工作顺顺当当' : ctx.scene === 'date' ? '约会开心' : '过得舒服'}。`,
    '再等我一小会儿，整理好就一起端上来。',
    '（还在想…）',
  ]
}
