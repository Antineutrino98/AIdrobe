"""把历史数据里的英文展示值改写成中文。

背景
----
早期版本的 prompt 要求模型输出英文（`description` 要 2-3 句英文、颜色写
`ivory`、细分写 `tulle midi skirt`）。prompt 后来改成强制中文，但老数据
仍然是英文，界面上只能靠前端遮挡。这个模块负责一次性把老数据改写干净。

安全边界（用户明确担心「原来要拿英文词的接口突然拿到中文词」）
------------------------------------------------------------
只改**展示字段**：
    subcategory / pattern / material_guess / colors[].name /
    description / style_tags / occasion_tags

绝不改**判定字段**：
    category、attributes、seasons、formality、warmth、material_confidence
    这些是后端用来比较、过滤、统计的枚举，写成中文会让筛选全线失配。

而且这些展示字段后端也不做等值比较（`_select` 只按 category /
attributes / formality / warmth / seasons 过滤，tag 匹配失败会自动降级），
所以改写它们不会让任何既有逻辑抛错。

翻不出来的段直接丢掉：宁可少一个细分，也不在界面上留英文。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger: logging.Logger = logging.getLogger(__name__)

_CJK = re.compile(r"[\u4e00-\u9fa5]")

# 长短语优先，避免 "ribbed knit" 被先拆掉。
PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), zh)
    for pattern, zh in (
        ('tulle midi skirt', '薄纱中长裙'),
        ('tulle mesh with ribbed knit waistband', '薄纱网纱配罗纹针织腰头'),
        ('striped ribbed sweater', '条纹罗纹针织衫'),
        ('slim straight jeans', '修身直筒牛仔裤'),
        ('wide[\\s-]?leg jeans', '阔腿牛仔裤'),
        ('long[\\s-]?sleeve shirt', '长袖衬衫'),
        ('short[\\s-]?sleeve shirt', '短袖衬衫'),
        ('short[\\s-]?sleeve tee', '短袖T恤'),
        ('oversized tee', '宽松T恤'),
        ('bomber jacket', '飞行员夹克'),
        ('chunky sneaker', '厚底运动鞋'),
        ('low[\\s-]?top sneaker', '低帮运动鞋'),
        ('track pants', '运动长裤'),
        ('lounge pants', '家居长裤'),
        ('casual pants', '休闲长裤'),
        ('cotton jersey', '棉质针织'),
        ('cotton twill', '棉质斜纹'),
        ('smooth cotton gabardine', '平滑棉华达呢'),
        ('textured woven fabric', '肌理梭织面料'),
        ('smooth leather[\\s-]?like with perforated toe and mesh side panels', '人造革，冲孔鞋头配网纱侧片'),
        ('mesh and smooth leather[\\s-]?like panels', '网纱与人造革拼接'),
        ('smooth leather', '平滑皮革'),
        ('smooth fabric', '平滑面料'),
        ('washed cotton', '水洗棉'),
        ('olive gr[ae]y', '橄榄灰'),
        ('olive green', '橄榄绿'),
        ('mint green', '薄荷绿'),
        ('heather gr[ae]y', '花灰'),
        ('faded blue', '褪色蓝'),
        ('medium blue', '中蓝'),
        ('dark brown', '深棕'),
        ('light pink', '浅粉'),
        ('light blue', '浅蓝'),
        ('white stripe', '白条纹'),
        ('navy blue', '藏青'),
        ('ribbed knit waistband', '罗纹针织腰头'),
        ('ribbed knit', '罗纹针织'),
        ('cotton blend', '棉混纺'),
        ('wool blend', '羊毛混纺'),
        ('cashmere blend', '羊绒混纺'),
        ('faux leather', '人造革'),
        ('tulle mesh', '薄纱网纱'),
        ('midi skirt', '中长裙'),
        ('mini skirt', '短裙'),
        ('maxi dress', '长款连衣裙'),
        ('wide[\\s-]?leg', '阔腿'),
        ('high[\\s-]?wais\\w*', '高腰'),
        ('long[\\s-]?sleeve', '长袖'),
        ('short[\\s-]?sleeve', '短袖'),
        ('sleeveless', '无袖'),
        ('t[\\s-]?shirt', 'T恤'),
    )
)

WORDS: dict[str, str] = {
    # 颜色
    "ivory": "象牙白", "cream": "米白", "off-white": "米白", "beige": "米色",
    "black": "黑色", "white": "白色", "gray": "灰色", "grey": "灰色",
    "navy": "藏青", "navy blue": "藏青", "blue": "蓝色", "light blue": "浅蓝",
    "dark blue": "深蓝", "denim blue": "牛仔蓝", "red": "红色", "green": "绿色",
    "brown": "棕色", "khaki": "卡其", "yellow": "黄色", "purple": "紫色",
    "orange": "橙色", "silver": "银色", "gold": "金色", "pink": "粉色",
    "pale pink": "浅粉", "charcoal": "炭灰", "burgundy": "酒红",
    "olive": "橄榄绿", "camel": "驼色", "tan": "棕褐", "light gray": "浅灰",
    "dark gray": "深灰", "multicolor": "多色", "multicolour": "多色",
    # 图案
    "stripe": "条纹", "striped": "条纹", "solid": "纯色", "plain": "纯色",
    "check": "格纹", "plaid": "格纹", "floral": "印花", "print": "印花",
    "dot": "波点", "polka dot": "波点", "geometric": "几何纹",
    "colorblock": "拼色", "colour block": "拼色", "none": "纯色",
    # 材质
    "cotton": "棉", "wool": "羊毛", "linen": "亚麻", "polyester": "聚酯纤维",
    "denim": "牛仔布", "silk": "真丝", "satin": "缎面", "leather": "皮革",
    "knit": "针织", "cashmere": "羊绒", "nylon": "尼龙", "velvet": "丝绒",
    "corduroy": "灯芯绒", "fleece": "抓绒", "chiffon": "雪纺", "suede": "麂皮",
    "tulle": "薄纱", "mesh": "网纱", "lace": "蕾丝", "tweed": "粗花呢",
    "polyamide": "锦纶", "elastane": "氨纶", "viscose": "黏胶",
    # 风格 / 场合
    "casual": "休闲", "work": "通勤", "office": "通勤", "date": "约会",
    "weekend": "周末", "travel": "旅行", "minimal": "极简",
    "minimalist": "极简", "streetwear": "街头", "smart casual": "商务休闲",
    "formal": "正式", "sporty": "运动", "athleisure": "运动休闲",
    "vintage": "复古", "preppy": "学院风", "business": "商务",
    "party": "聚会", "outdoor": "户外", "lounge": "居家", "elegant": "优雅",
    "classic": "经典", "feminine": "女性化", "romantic": "浪漫",
    "sweet": "甜美", "relaxed": "松弛", "cozy": "舒适", "chic": "时髦",
    # 品类 / 细分常见词
    "shirt": "衬衫", "blouse": "衬衫", "sweater": "针织衫", "hoodie": "卫衣",
    "vest": "马甲", "tank top": "吊带", "blazer": "西装外套",
    "jacket": "夹克", "coat": "大衣", "trench coat": "风衣",
    "trousers": "西裤", "pants": "长裤", "jeans": "牛仔裤", "shorts": "短裤",
    "skirt": "半身裙", "dress": "连衣裙", "jumpsuit": "连体裤",
    "sneakers": "运动鞋", "boots": "靴", "loafers": "乐福鞋",
    "sandals": "凉鞋", "heels": "高跟鞋", "flats": "平底鞋",
    "cardigan": "开衫", "overcoat": "大衣", "parka": "派克大衣",
    "cropped": "短款", "oversized": "宽松款", "fitted": "合身",
    "slim": "修身", "regular": "常规", "loose": "宽松", "pleated": "百褶",
    "waistband": "腰头", "elastic": "弹力", "sheer": "透视", "ribbed": "罗纹",
    "longline": "长款", "tailored": "剪裁利落", "structured": "挺括",
    'athletic': '运动',
    'chunky': '厚底',
    'distressed': '做旧',
    'everyday': '日常',
    'daily': '日常',
    'graphic': '图案',
    'home': '居家',
    'joggers': '束脚裤',
    'loafer': '乐福鞋',
    'long_sleeve': '长袖',
    'loungewear': '家居服',
    'relaxed': '松弛',
    'retro': '复古',
    'soft': '柔软',
    'streetwear': '街头',
    'workwear': '工装',
    'trench': '风衣',
    'athleisure': '运动休闲',
    'sneaker': '运动鞋',
    'sneakers': '运动鞋',
    'tee': 'T恤',
    'color_block': '拼色',
    'pants': '长裤',
    'stripe': '条纹',
    'check': '格纹',
    'offic': '通勤',
    'casual': '休闲',
}


def _split(value: str) -> list[str]:
    return [part for part in re.split(r"\s*[/,、]\s*", value) if part]


def translate_text(value: Any) -> str | None:
    """把一个自由文本值翻成中文；翻不出来返回 None（调用方据此留空）。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if _CJK.search(raw):
        return raw  # 已经是中文，原样保留（可能混英文，交给前端再兜一层）

    for pattern, zh in PHRASES:
        if pattern.search(raw):
            return zh

    pieces: list[str] = []
    for part in _split(raw):
        key = part.strip().lower()
        zh = WORDS.get(key)
        if zh:
            pieces.append(zh)
            continue
        # 逐词翻译，能翻几个算几个
        tokens = [WORDS.get(tok, "") for tok in re.split(r"[\s\-]+", key)]
        tokens = [tok for tok in tokens if tok]
        if tokens:
            pieces.append("".join(tokens))
    if not pieces:
        return None
    # 保序去重
    seen: set[str] = set()
    unique = []
    for piece in pieces:
        if piece not in seen:
            seen.add(piece)
            unique.append(piece)
    return "、".join(unique[:4])


def translate_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result = [zh for zh in (translate_text(item) for item in value) if zh]
    return result or None


async def rewrite_english_values(session: AsyncSession) -> dict[str, int]:
    """扫描全部衣物，把展示字段里的英文改写成中文。幂等。"""
    from app.models import Garment

    rows = (await session.execute(select(Garment))).scalars().all()
    stats: dict[str, int] = {
        "garments": len(rows),
        "subcategory": 0,
        "pattern": 0,
        "material_guess": 0,
        "colors": 0,
        "description": 0,
        "style_tags": 0,
        "occasion_tags": 0,
    }

    for garment in rows:
        changed = False

        for field in ("subcategory", "pattern", "material_guess"):
            current = getattr(garment, field)
            if current and not _CJK.search(str(current)):
                setattr(garment, field, translate_text(current))
                stats[field] += 1
                changed = True

        # colors: [{name, hex, role}] —— 只动 name
        colors = garment.colors
        if isinstance(colors, list) and colors:
            new_colors: list[dict[str, Any]] = []
            touched = False
            for entry in colors:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    if name and not _CJK.search(str(name)):
                        zh = translate_text(name)
                        if zh:
                            entry = {**entry, "name": zh}
                            touched = True
                        else:
                            touched = True  # 翻不出来就丢掉这个名字
                            entry = {**entry, "name": None}
                new_colors.append(entry)
            if touched:
                garment.colors = [c for c in new_colors if c.get("name")]
                stats["colors"] += 1
                changed = True

        for field in ("style_tags", "occasion_tags"):
            new_list = translate_list(getattr(garment, field))
            if new_list is not None and new_list != getattr(garment, field):
                setattr(garment, field, new_list)
                stats[field] += 1
                changed = True

        raw = garment.vision_raw
        if isinstance(raw, dict):
            desc = raw.get("description")
            if desc and not _CJK.search(str(desc)):
                zh_desc = translate_text(desc)
                garment.vision_raw = {
                    **raw,
                    "description": zh_desc or None,
                }
                stats["description"] += 1
                changed = True

        if changed:
            pass

    await session.commit()
    logger.info("英文展示值改写完成：%s", stats)
    return stats
