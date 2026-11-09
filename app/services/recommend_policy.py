"""用户可设定的推荐 / 穿着策略。

存在 `users.settings["recommend_policy"]`，由前端「推荐策略」面板读写。
所有策略只影响**进候选池之前的过滤与排序**，不改数据库、不改 M1 契约。

设计动机：用户「决定穿了某一套」时，`POST /feedback(signal=worn)` 已经给
该套单品追加了今天的穿着日期。但「穿过之后多久能再出现」应该是用户说了算——
有人愿意连着穿，有人昨天穿过今天就不想再看到，所以做成可选策略。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from app.schemas import GarmentSchema

POLICY_KEY: str = "recommend_policy"
BLOCKED_WEAR_STATES: frozenset[str] = frozenset({"dirty", "laundry"})

REWEAR_CHOICES: dict[str, str] = {
    "anytime": "随时可再穿（刚穿过、没洗也能接着穿）",
    "no_yesterday": "昨天或今天穿过的，今天不再推荐",
    "rest_2": "穿过后至少隔 2 天",
    "rest_3": "穿过后至少隔 3 天",
    "rest_7": "穿过后至少隔 7 天",
}

# 上班/正装场合的正式度要求。只影响 work 场景的检索条件。
DRESS_CODE_CHOICES: dict[str, str] = {
    "business": "很商务（正装为主）",
    "smart_casual": "商务休闲（可以稍微放松）",
    "free": "无着装要求（怎么舒服怎么穿）",
}

PREFER_CHOICES: dict[str, str] = {
    "balanced": "均衡（不特别偏好）",
    "least_worn": "优先推荐最近穿得少的",
    "newest": "优先推荐最近入库的",
}

# 00:51 用户明确：正式度 / 保暖度区间很重要，不能为了让鞋进池就绕开。
# 但「库里没有符合要求的单品时怎么办」应该由用户决定。
POOL_FALLBACK_CHOICES: dict[str, str] = {
    "strict": "严格：宁可少给或留空，也不放宽条件",
    "moderate": "适中：条件太窄时逐级放宽一级，尽量给出搭配",
    "relaxed": "勉强推荐：放宽条件，尽量凑出几套",
}

# 01:56 用户新增：怕冷 / 怕热各三档。这是「体感偏好」，只作为信息
# 交给 Planner 与 Stylist 调整厚薄，不做程序硬规则、不改品类过滤。
COLD_SENSITIVITY_CHOICES: dict[str, str] = {
    "normal": "常规（按正常厚度）",
    "mild": "有点怕冷（略偏厚）",
    "strong": "很怕冷（明显偏厚、更保暖）",
}

HEAT_SENSITIVITY_CHOICES: dict[str, str] = {
    "normal": "常规（按正常厚度）",
    "mild": "有点怕热（略偏薄）",
    "strong": "很怕热（明显偏薄、更透气）",
}


@dataclass(frozen=True)
class RecommendPolicy:
    """一组完整策略；所有字段都有安全默认值。"""

    rewear: str = "anytime"
    prefer: str = "balanced"
    outfit_count: int = 3
    # work 场景的正式度要求：business / smart_casual / free
    dress_code: str = "smart_casual"
    # 区间筛完没有合适单品时：strict=留空 / moderate=逐级放宽 / relaxed=全放宽
    pool_fallback: str = "moderate"
    # 体感偏好，各三档；只交给模型参考，不参与任何程序过滤
    cold_sensitivity: str = "normal"
    heat_sensitivity: str = "normal"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_POLICY: RecommendPolicy = RecommendPolicy()


def parse_policy(settings: dict[str, Any] | None) -> RecommendPolicy:
    """从 users.settings 里取策略；遇到非法值一律回退到默认，绝不抛错。"""
    raw = (settings or {}).get(POLICY_KEY)
    if not isinstance(raw, dict):
        return DEFAULT_POLICY
    rewear = raw.get("rewear", DEFAULT_POLICY.rewear)
    if rewear not in REWEAR_CHOICES:
        rewear = DEFAULT_POLICY.rewear
    prefer = raw.get("prefer", DEFAULT_POLICY.prefer)
    if prefer not in PREFER_CHOICES:
        prefer = DEFAULT_POLICY.prefer
    try:
        outfit_count = int(raw.get("outfit_count", DEFAULT_POLICY.outfit_count))
    except (TypeError, ValueError):
        outfit_count = DEFAULT_POLICY.outfit_count
    outfit_count = min(5, max(3, outfit_count))
    dress_code = raw.get("dress_code", DEFAULT_POLICY.dress_code)
    if dress_code not in DRESS_CODE_CHOICES:
        dress_code = DEFAULT_POLICY.dress_code
    pool_fallback = raw.get("pool_fallback", DEFAULT_POLICY.pool_fallback)
    if pool_fallback not in POOL_FALLBACK_CHOICES:
        pool_fallback = DEFAULT_POLICY.pool_fallback
    cold = raw.get("cold_sensitivity", DEFAULT_POLICY.cold_sensitivity)
    if cold not in COLD_SENSITIVITY_CHOICES:
        cold = DEFAULT_POLICY.cold_sensitivity
    heat = raw.get("heat_sensitivity", DEFAULT_POLICY.heat_sensitivity)
    if heat not in HEAT_SENSITIVITY_CHOICES:
        heat = DEFAULT_POLICY.heat_sensitivity
    return RecommendPolicy(
        rewear=rewear,
        prefer=prefer,
        outfit_count=outfit_count,
        dress_code=dress_code,
        pool_fallback=pool_fallback,
        cold_sensitivity=cold,
        heat_sensitivity=heat,
    )


def _worn_dates(garment: GarmentSchema) -> list[str]:
    return [value for value in (garment.recent_wear_dates or []) if isinstance(value, str)]


def _is_blocked_by_rewear(
    garment: GarmentSchema, rewear: str, today: date
) -> bool:
    if rewear == "anytime":
        return False
    dates = _worn_dates(garment)
    if not dates:
        return False

    if rewear == "no_yesterday":
        yesterday = (today - timedelta(days=1)).isoformat()
        return any(value >= yesterday for value in dates)

    days = int(rewear.split("_", maxsplit=1)[1])
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    return any(value >= cutoff for value in dates)


def apply_policy(
    garments: list[GarmentSchema],
    policy: RecommendPolicy,
    *,
    today: date | None = None,
) -> list[GarmentSchema]:
    """按策略过滤并排序衣物；返回新列表，不修改入参。"""
    current_day = today or date.today()
    result: list[GarmentSchema] = []
    for garment in garments:
        # 常识，不给用户选：该洗了 / 洗衣中的永远不进候选池。
        # 「穿过一次」不算脏，是否还能穿完全由 rewear 策略决定。
        if garment.wear_state in BLOCKED_WEAR_STATES:
            continue
        if _is_blocked_by_rewear(garment, policy.rewear, current_day):
            continue
        result.append(garment)

    if policy.prefer == "least_worn":
        cutoff = (current_day - timedelta(days=30)).isoformat()
        result.sort(
            key=lambda item: sum(
                1 for value in _worn_dates(item) if value >= cutoff
            )
        )
    elif policy.prefer == "newest":
        result.sort(key=lambda item: item.created_at, reverse=True)
    return result


# 每个正式度档位给 planner 的一句话约束（只对 work 场景生效）。
DRESS_CODE_HINTS: dict[str, str] = {
    "business": (
        "工作场景要求正式：formality_min 不低于 4，"
        "优先衬衫、西裤、西装外套、皮质鞋；排除运动、家居、街头风格。"
    ),
    "smart_casual": (
        "工作场景为商务休闲：以 formality 2-3 为主，"
        "可以用针织、休闲西裤、干净的运动鞋，但避免睡衣、健身服、拖鞋。"
    ),
    "free": (
        "工作场景无着装要求：以舒适和好看为先，"
        "不必刻意提高正式度。"
    ),
}


def dress_code_hint(dress_code: str, scene: str) -> str:
    """只在 work 场景返回约束；其它场景一律空串。"""
    if scene != "work":
        return ""
    return DRESS_CODE_HINTS.get(dress_code, DRESS_CODE_HINTS["smart_casual"])
