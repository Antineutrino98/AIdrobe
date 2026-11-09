"""服装属性维度定义 —— M2 / M3 共同依赖的共享契约（M1 交付）。

文档依据：新版 M1.md「app/attribute_dimensions.py（共享契约）」。
2026-11-05 改版说明：新版文档把 6 个维度的中文显示名写死了
（长度 / 领型 / 领口样式 / 裤型 / 闭合方式 / 纹理），本文件已逐字对齐；
10 个英文键与各自 values 与上一版完全一致，未改动。

范围（已与用户确认，2026-11-05）
--------------------------------
ATTRIBUTE_DIMENSIONS **严格只含** M1.md 逐字列出的 10 个扩展维度，
即 garments.attributes(JSONB) 允许出现的键；每个维度的 values 也逐字照抄文档。
不添加、不合并、不推断任何文档没给出的值。

CATEGORY_VALUES：用户 2026-11-05 明确要求「保留 48 类」，
依据 GB/T 23560-2009 + Google Product Taxonomy；新版 M1.md 正文里
写的 6 个值（top/bottom/outerwear/shoes/accessory/dress）按用户指示
理解为举例，不退回成 6 类枚举。

以下字段**不进字典**，原因一并记录，避免后续模块误解：
- category / subcategory / pattern / seasons / style_tags / occasion_tags /
  colors.role / material_guess：M1.md 只给了「例如」清单，没有完整合法值，
  按用户选择 2.A 保持自由文本；它们的列名放在 CORE_COLUMNS 里。
- status / signal：合法值已冻结，但按 2.A 用独立 Python 枚举 + 数据库 CHECK 约束
  实现（见 app/schemas.py 与 app/models.py），它们是「状态/反馈」而非「可选检索维度」。

后续模块契约提示（本模块不实现任何 M2/M3 逻辑，仅留说明）
--------------------------------------------------------
- M2 的 vision prompt 必须从本文件读取可选键与合法值，禁止硬编码维度值。
- M3 生成检索计划时的「可选维度清单」同样来自本文件。
- attributes 是 JSONB：以后新增维度只改本文件 + 数据，不需要改数据库迁移。
"""
from __future__ import annotations

from typing import Final, TypedDict


class DimensionSpec(TypedDict):
    """单个维度：合法值列表 + 中文显示名。"""

    values: list[str]
    label_zh: str


ATTRIBUTE_DIMENSIONS: Final[dict[str, DimensionSpec]] = {
    "fit_type": {
        "values": ["slim", "regular", "loose", "oversized"],
        "label_zh": "版型",
    },
    "length": {
        "values": ["crop", "regular", "long", "maxi"],
        "label_zh": "长度",
    },
    "sleeve_length": {
        "values": ["sleeveless", "short", "three_quarter", "long"],
        "label_zh": "袖长",
    },
    "neckline": {
        "values": ["crew", "v_neck", "collar", "hooded", "turtleneck", "other"],
        "label_zh": "领型",
    },
    "collar_style": {
        "values": ["stand", "spread", "lapel", "notched", "none"],
        "label_zh": "领口样式",
    },
    "pant_leg_style": {
        "values": ["straight", "skinny", "wide", "bootcut", "tapered"],
        "label_zh": "裤型",
    },
    "waist_rise": {
        "values": ["low", "mid", "high"],
        "label_zh": "腰线",
    },
    "silhouette": {
        "values": ["a_line", "h_line", "x_line", "oversized", "fitted"],
        "label_zh": "廓形",
    },
    "closure": {
        "values": ["button", "zip", "pullover", "tie", "none"],
        "label_zh": "闭合方式",
    },
    "texture": {
        "values": ["smooth", "knit", "ribbed", "quilted", "fuzzy", "other"],
        "label_zh": "纹理",
    },
}

# 逐字照抄 M1.md 给出的 CORE_COLUMNS，不增删。
# 注意：material_confidence 虽然也是核心列，但不在文档这份清单里，故不擅自补入。
CORE_COLUMNS: Final[list[str]] = [
    "category",
    "subcategory",
    "colors",
    "formality",
    "seasons",
    "warmth",
    "style_tags",
    "occasion_tags",
    "pattern",
    "material_guess",
]


# ---------------------------------------------------------------------------
# category 合法值（M2，2026-11-05 用户确认「48 类」）
#
# 依据（已联网核实）：
#   - GB/T 23560-2009《服装分类代码》现行国标，第二层按人体部位/基本用途划分；
#   - GB/T 37600.13-2018《全国主要产品分类 产品类别核心元数据 第13部分：服装》；
#   - Google Product Taxonomy：Apparel & Accessories > Clothing (1604) 的 18 个
#     直接子类，以及 Shoes(187) / Jewelry(188) / Clothing Accessories(167) 等。
#
# 设计：AI 优先选具体类；判断不出选本组的「通用类」；实在不匹配归 `other`。
# `other` 对应国标每层的「99 收容类目」，保证任何一件衣物都能归类，
# 不会因为没有对应品类而入库失败。
#
# 注意：这是**独立常量**，不并入 ATTRIBUTE_DIMENSIONS
#（后者保持用户确认的 10 个扩展维度键不变）。M2/M3 禁止另写一份硬编码清单。
# ---------------------------------------------------------------------------
CATEGORY_VALUES: Final[dict[str, str]] = {
    # 上装 8
    "top": "上装（通用）",
    "t_shirt": "T恤",
    "shirt": "衬衫",
    "sweater": "毛衣/针织衫",
    "hoodie": "卫衣",
    "vest": "马甲/背心",
    "tank_top": "吊带/汗背心",
    "base_layer": "保暖内衣/打底",
    # 下装 5
    "bottom": "下装（通用）",
    "trousers": "西裤/休闲裤",
    "jeans": "牛仔裤",
    "shorts": "短裤",
    "skirt": "半身裙/裙裤",
    # 连身 4
    "dress": "连衣裙",
    "jumpsuit": "连体衣",
    "suit": "西服/套装",
    "robe": "袍/旗袍",
    # 外套 7
    "outerwear": "外套（通用）",
    "coat": "大衣/风衣",
    "jacket": "夹克",
    "blazer": "西装外套",
    "down_jacket": "羽绒服",
    "cape": "披风/斗篷",
    "raincoat": "雨衣",
    # 场景 9
    "activewear": "运动服",
    "swimwear": "泳装",
    "sleepwear": "家居服/睡衣",
    "underwear": "内衣",
    "socks": "袜",
    "traditional": "民族/传统服装",
    "ceremonial": "婚纱礼服",
    "uniform": "制服/职业装",
    "costume": "戏服/演出服",
    # 鞋 5
    "shoes": "鞋（通用）",
    "sneakers": "运动鞋",
    "boots": "靴",
    "sandals": "凉鞋",
    "slippers": "拖鞋",
    # 配饰 9
    "accessory": "配饰（通用）",
    "hat": "帽/头饰",
    "scarf": "围巾/披肩",
    "tie": "领带/领结",
    "gloves": "手套",
    "belt": "腰带",
    "bag": "箱包",
    "jewelry": "首饰",
    "eyewear": "眼镜",
    # 兜底 1（国标 99 收容类目）
    "other": "其他（收容类目）",
}

CATEGORY_FALLBACK: Final[str] = "other"
