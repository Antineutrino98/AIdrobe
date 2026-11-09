"""候选池粗筛（U1）。

职责边界
--------
本模块只做「全局筛选 + 排序 + 截断」，**不做任何审美判断**，也不调用模型。
审美与组合全部属于 MULTIMODAL_STYLIST（见 stylist_llm.py）。

U1 重构
-------
旧实现按 slot 分桶检索（每个桶 10 件、每桶必须覆盖），等于让文本模型
替搭配模型决定了「一套必须有几件上装几件下装」。现在废除全部分桶逻辑：

1. 收到 Planner 的**全局约束**（正式度区间 / 保暖度区间），从整份衣橱里
   一次性捞出所有符合的单品，形成**一个混合候选池**。
2. 排序按「最近 30 天穿着次数升序」（优先推荐冷门衣物），同次数随机扰动。
3. 截断到 `POOL_LIMIT`。
4. 用轮转取样保证品类多样性：池子不会清一色全是上衣。
5. 区间筛完候选太少时自动放宽，并记一条说明返回给前端。

硬性保留
--------
- `is_recommendable`：`status='owned'` 且 `wear_state` 不是 dirty / laundry。
- 正式度 / 保暖度为 null 的单品**保留**（缺数据不淘汰）。
- 属性明确冲突必须拒绝（M3 契约）；全局池不用属性条件，因此不冲突。
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from app.schemas import GarmentSchema, GarmentStatus, WearState

logger: logging.Logger = logging.getLogger(__name__)

# 候选池上限。U1 规定默认 30 件；真正发给模型的图片另受
# STYLIST_MAX_IMAGES 控制（默认 6 张），两者互不影响。
POOL_LIMIT: int = 30
RECENT_WINDOW_DAYS: int = 30
BLOCKED_WEAR_STATES: frozenset[WearState] = frozenset(
    {WearState.DIRTY, WearState.LAUNDRY}
)

# 品类归类**只用于多样性检查**，不是槽位，也不要求每组都出现。
GROUP_CATEGORIES: dict[str, list[str]] = {
    "tops": [
        "top", "t_shirt", "shirt", "sweater", "hoodie",
        "vest", "tank_top", "base_layer",
    ],
    "one_piece": [
        "dress", "jumpsuit", "robe", "suit",
        "ceremonial", "traditional", "uniform", "activewear",
        "sleepwear", "swimwear",
    ],
    "bottoms": ["bottom", "trousers", "jeans", "shorts", "skirt", "socks"],
    "outer": [
        "outerwear", "coat", "jacket", "blazer", "down_jacket",
        "cape", "raincoat",
    ],
    "shoes": ["shoes", "sneakers", "boots", "sandals", "slippers"],
    "accessory": [
        "accessory", "hat", "scarf", "tie", "gloves", "belt",
        "bag", "jewelry", "eyewear",
    ],
    "other": ["other", "underwear", "costume"],
}

# 一件即可成套的品类（连衣裙、连体裤、套装…）。
ONE_PIECE_CATEGORIES: frozenset[str] = frozenset(GROUP_CATEGORIES["one_piece"])

GROUP_LABELS_ZH: dict[str, str] = {
    "tops": "上装", "one_piece": "连衣裙/套装", "bottoms": "下装",
    "outer": "外套", "shoes": "鞋", "accessory": "配饰", "other": "其他",
}


def category_group(category: str | None) -> str:
    """把品类归到一个大类，仅用于多样性统计。"""
    key = str(category or "").strip().lower()
    for name, categories in GROUP_CATEGORIES.items():
        if key in categories:
            return name
    return "other"


@dataclass
class CandidatePool:
    """一个混合候选池（已截断）。没有任何槽位概念。"""

    items: list[GarmentSchema] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    conditions_used: dict[str, Any] = field(default_factory=dict)


def is_recommendable(garment: GarmentSchema) -> bool:
    """owned 且不是 dirty / laundry。"""
    return (
        garment.status == GarmentStatus.OWNED
        and garment.wear_state not in BLOCKED_WEAR_STATES
    )


def recent_wear_count(
    garment: GarmentSchema,
    days: int = RECENT_WINDOW_DAYS,
    *,
    today: date | None = None,
) -> int:
    """recent_wear_dates 中最近 N 天的穿着次数。"""
    current = today or date.today()
    cutoff: str = (current - timedelta(days=days)).isoformat()
    return sum(1 for value in garment.recent_wear_dates if value >= cutoff)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def match_conditions(
    garment: GarmentSchema, conditions: dict[str, Any]
) -> tuple[bool, int]:
    """通用条件匹配，返回 (是否匹配, attributes 命中数)。

    U1 之后主链路不再按条件分桶，但 M4 的历史校验与单测仍依赖这个工具。
    数值/标签字段为 null 或缺失时保留候选；属性明确冲突必须拒绝。
    """
    matched_attributes: int = 0
    categories = conditions.get("category")
    if isinstance(categories, list) and garment.category not in categories:
        return False, 0

    seasons = conditions.get("season")
    if isinstance(seasons, list):
        if "all" not in garment.seasons and not (set(seasons) & set(garment.seasons)):
            return False, 0

    for key, column in (
        ("formality_min", garment.formality),
        ("formality_max", garment.formality),
        ("warmth_min", garment.warmth),
        ("warmth_max", garment.warmth),
    ):
        bound = _as_number(conditions.get(key))
        if bound is None or column is None:
            continue
        if key.endswith("_min") and column < bound:
            return False, 0
        if key.endswith("_max") and column > bound:
            return False, 0

    for key in ("style_tags_any", "occasion_tags_any"):
        wanted = conditions.get(key)
        present = (
            garment.style_tags if key == "style_tags_any" else garment.occasion_tags
        )
        if isinstance(wanted, list) and present:
            if not (set(wanted) & set(present)):
                return False, 0

    attributes = conditions.get("attributes")
    if isinstance(attributes, dict):
        for attr_key, expected in attributes.items():
            if not isinstance(attr_key, str) or not isinstance(expected, str):
                continue
            if attr_key not in garment.attributes:
                continue  # 缺键保留
            if garment.attributes[attr_key] != expected:
                return False, 0
            matched_attributes += 1
    return True, matched_attributes


def _level_ok(value: int | None, bounds: Any) -> bool:
    """缺数据不淘汰；有数据时必须落在 Planner 给的区间内。"""
    if value is None:
        return True
    low = getattr(bounds, "min", 1)
    high = getattr(bounds, "max", 5)
    return low <= value <= high


@dataclass(frozen=True)
class _Range:
    min: int
    max: int


# 「适中」档：候选少到这个数就逐级放宽区间，避免温度 / 正式度合适
# 却因为区间太窄一件都选不出来（用户 01:56 反馈）。
MODERATE_MIN_ITEMS: int = 4


def _widen(bounds: Any, step: int) -> Any:
    """把正式度 / 保暖度区间的上下界各外扩 step 级，钳在 1..5。"""
    if bounds is None:
        return None
    return _Range(
        max(1, getattr(bounds, "min", 1) - step),
        min(5, getattr(bounds, "max", 5) + step),
    )


def _by_least_worn(
    garments: Iterable[GarmentSchema],
    rng: random.Random,
    *,
    today: date | None,
) -> list[GarmentSchema]:
    """最近 30 天穿着次数升序；同次数随机扰动，避免每次结果一模一样。"""
    decorated = [
        (garment, recent_wear_count(garment, today=today), rng.random())
        for garment in garments
    ]
    decorated.sort(key=lambda item: (item[1], item[2]))
    return [item[0] for item in decorated]


def _diversify(
    ranked: list[GarmentSchema], limit: int, notes: list[str]
) -> list[GarmentSchema]:
    """轮转取样：先每个大类各取一件，再按冷门优先补足。

    U1 要求「如果截断后全是上衣，应尝试随机补充几件下装或连衣裙」。
    轮转法让这件事变成结构保证，而不是事后打补丁。
    """
    if limit <= 0 or not ranked:
        return []
    buckets: dict[str, list[GarmentSchema]] = {}
    for garment in ranked:
        buckets.setdefault(category_group(garment.category), []).append(garment)

    selected: list[GarmentSchema] = []
    for group in sorted(buckets):
        if buckets[group] and len(selected) < limit:
            selected.append(buckets[group].pop(0))

    chosen_ids = {item.id for item in selected}
    for garment in ranked:
        if len(selected) >= limit:
            break
        if garment.id in chosen_ids:
            continue
        selected.append(garment)
        chosen_ids.add(garment.id)

    groups = {category_group(item.category) for item in selected}
    if len(groups) < 3 and len(ranked) > len(selected):
        notes.append(
            "衣橱里可用的品类不多，候选池可能偏单一；搭配会如实给出不完整的方案。"
        )
    return selected


def build_global_pool(
    garments: list[GarmentSchema],
    plan: Any,
    limit: int = POOL_LIMIT,
    rng: random.Random | None = None,
    *,
    today: date | None = None,
    pool_fallback: str = "strict",
) -> CandidatePool:
    """从整份衣橱一次性粗筛出混合候选池。不分组、不分槽位。

    `pool_fallback` 由用户策略决定：
    - `strict`：正式度 / 保暖度区间硬筛，筛完不够就如实少给或留空；
    - `moderate`（默认）：候选太少时把区间逐级外扩一级，优先保留较窄的区间；
    - `relaxed`：筛完不够时放宽到全部可穿单品，宁可勉强给几套。

    01:30 用户明确：温度只交给提示词提醒「注意温度与薄厚的关系」，
    **程序不按温度过滤品类、不设硬规则**。这里只做用户自己设定的策略筛选。
    """
    random_source = rng or random.Random()
    notes: list[str] = []
    base = [item for item in garments if is_recommendable(item)]

    formality = getattr(plan, "formality_range", None)
    warmth = getattr(plan, "warmth_range", None)
    matched = [
        item
        for item in base
        if _level_ok(item.formality, formality) and _level_ok(item.warmth, warmth)
    ]
    conditions_used: dict[str, Any] = {
        "formality_range": getattr(formality, "model_dump", lambda: {})()
        if formality is not None
        else {},
        "warmth_range": getattr(warmth, "model_dump", lambda: {})()
        if warmth is not None
        else {},
        "pool_fallback": pool_fallback,
    }

    # 只有用户选「勉强推荐」时才放宽：不仅「筛完几乎空了」要放宽，
    # 「某个大类（例如鞋）整体被区间挡掉」也要放宽，否则用户会在
    # 衣橱明明有鞋的情况下拿到没鞋的搭配。严格模式一律不放宽。
    if pool_fallback == "relaxed":
        matched_groups = {
            category_group(str(item.category or "").strip().lower())
            for item in matched
        }
        base_groups = {
            category_group(str(item.category or "").strip().lower())
            for item in base
        }
        missing_groups = base_groups - matched_groups
        if (len(matched) < 2 or missing_groups) and len(base) > len(matched):
            logger.info(
                "用户选择放宽：入选 %d 件，缺失大类 %s，改用全部可穿单品",
                len(matched),
                sorted(missing_groups) or "无",
            )
            notes.append("已按策略放宽正式度与保暖度条件，尽量保留衣橱里已有的品类。")
            matched = base
            conditions_used["relaxed"] = True
    elif pool_fallback == "moderate" and len(matched) < MODERATE_MIN_ITEMS:
        # 逐级外扩，先试 1 级、不够再 2 级……尽量保留 Planner 原本的区间意图。
        for step in (1, 2, 3, 4):
            wide_formality = _widen(formality, step)
            wide_warmth = _widen(warmth, step)
            widened = [
                item
                for item in base
                if _level_ok(item.formality, wide_formality)
                and _level_ok(item.warmth, wide_warmth)
            ]
            if len(widened) > len(matched):
                logger.info(
                    "适中策略：区间外扩 %d 级，候选 %d → %d 件",
                    step,
                    len(matched),
                    len(widened),
                )
                notes.append(
                    "按「适中」策略逐级放宽正式度与保暖度区间，避免条件过窄没有可搭配的单品。"
                )
                matched = widened
                conditions_used["widened_by"] = step
            if len(matched) >= MODERATE_MIN_ITEMS or step == 4:
                break
    elif len(matched) < 2:
        notes.append("按当前正式度与保暖度要求，衣橱里能用的单品很少；已如实保留。")

    ranked = _by_least_worn(matched, random_source, today=today)
    selected = _diversify(ranked, limit, notes)

    # 缺哪些大类，明确告诉前端（用中文，不用旧槽位名）。
    missing_groups = [
        GROUP_LABELS_ZH[name]
        for name in ("bottoms", "one_piece", "shoes", "outer", "tops")
        if not any(category_group(item.category) == name for item in selected)
        and any(category_group(item.category) == name for item in base)
    ]
    if missing_groups:
        logger.info("候选池未覆盖的品类：%s", "、".join(missing_groups))

    logger.info(
        "候选池构建完成：可穿 %d 件 → 入选 %d 件，覆盖 %d 个大类",
        len(base),
        len(selected),
        len({category_group(item.category) for item in selected}),
    )
    return CandidatePool(
        items=selected, notes=notes, conditions_used=conditions_used
    )
