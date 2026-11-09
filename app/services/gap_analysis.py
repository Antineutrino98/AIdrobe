"""M4 流程 D：衣橱缺口发现。

数据依据（M4.md「流程 D」）
---------------------------
1. 结构性缺口（U1 后）：按品类大类统计，某个大类在 owned 衣物里一件都没有。
   不再读取历史会话里已废弃的槽位字段。
2. 场景覆盖薄弱：近 30 天 scene=X 的请求里「可搭配套数 < 3」的比例 ≥ 50%。

关于 feasible_outfit_count 的近似
---------------------------------
M1 的 outfit_sessions 表**没有** feasible_outfit_count 列（只有
recommendations JSONB）。本模块用 recommendations 里 match_score ≥ 6 的
套数作为该次请求的「可搭配套数」，与流程 B 的合格线一致。
这是有依据的近似，不会为了本模块改表或新增迁移。

统计不足时不编造缺口：gaps 至少会有一条基于真实 counts 的中文说明，
措辞只陈述数据支持的内容（gap_summary.md 也要求「不夸大」）。
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from app.llm_config import ModelRole
from app.prompts import load_prompt
from app.schemas import (
    GapReportResponse,
    GarmentSchema,
    GarmentStatus,
    OutfitSessionSchema,
)
from app.services.duplicate_check import (
    PlannerCallError,
    PlannerConfigError,
    call_text_planner,
)
from app.services.filter import GROUP_CATEGORIES

logger: logging.Logger = logging.getLogger(__name__)

LOOKBACK_DAYS: int = 30
WEAK_SCENE_RATIO: float = 0.5
MIN_FEASIBLE_OUTFITS: int = 3
MIN_MATCH_SCORE: int = 6


# 品类大类的中文名，只用于生成缺口描述，不是槽位。
GROUP_LABELS: dict[str, str] = {
    "tops": "上装",
    "one_piece": "连衣裙 / 套装",
    "bottoms": "下装",
    "outer": "外套",
    "shoes": "鞋",
    "accessory": "配饰",
    "other": "其他",
}


def _scene_of(session: OutfitSessionSchema) -> str | None:
    context = session.request_context
    if isinstance(context, dict):
        scene = context.get("scene")
        if isinstance(scene, str) and scene.strip():
            return scene.strip()
    return None


def _feasible_count(session: OutfitSessionSchema) -> int:
    """用 score>=6 的搭配数近似该次请求的 feasible_outfit_count。"""
    recommendations = session.recommendations
    if not isinstance(recommendations, list):
        return 0
    count = 0
    for entry in recommendations:
        if not isinstance(entry, dict):
            continue
        try:
            score = int(entry.get("match_score", 0))
        except (TypeError, ValueError):
            score = 0
        if score >= MIN_MATCH_SCORE:
            count += 1
    return count


def _temperature_of(session: OutfitSessionSchema) -> float | None:
    context = session.request_context
    if isinstance(context, dict):
        raw = context.get("temperature_override")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    return None


def compute_statistics(
    garments: list[GarmentSchema], sessions: list[OutfitSessionSchema]
) -> dict[str, Any]:
    """纯函数：把衣物与近 30 天会话压成统计 + 具体缺口描述。"""
    owned_counts: Counter[str] = Counter(
        garment.category
        for garment in garments
        if garment.status == GarmentStatus.OWNED
    )

    scene_counts: dict[str, list[int]] = defaultdict(list)
    scene_temperatures: dict[str, list[float]] = defaultdict(list)
    temperatures: list[float] = []
    for session in sessions:
        scene = _scene_of(session)
        temperature = _temperature_of(session)
        if scene:
            scene_counts[scene].append(_feasible_count(session))
            if temperature is not None:
                scene_temperatures[scene].append(temperature)
        if temperature is not None:
            temperatures.append(temperature)

    scene_stats: dict[str, dict[str, float]] = {}
    for scene, feasible in scene_counts.items():
        weak = sum(1 for count in feasible if count < MIN_FEASIBLE_OUTFITS)
        ratio = weak / len(feasible) if feasible else 0.0
        scene_stats[scene] = {
            "requests": len(feasible),
            "weak_requests": weak,
            "weak_ratio": round(ratio, 3),
            "avg_feasible_outfits": round(sum(feasible) / len(feasible), 2),
        }

    gaps: list[str] = []
    # 1) 结构性缺口：按品类大类统计，U1 后不再依赖任何槽位字段。
    #    自由组合下缺的是「某一类可选项」，不是「某个必须填的格子」。
    for group, categories in GROUP_CATEGORIES.items():
        if group in {"accessory", "other"}:
            continue
        present = sum(owned_counts.get(category, 0) for category in categories)
        if present == 0:
            gaps.append(
                f"衣橱里没有{GROUP_LABELS.get(group, group)}类单品，"
                f"搭配时可选的自由组合会明显减少，建议补一件基础款。"
            )

    # 2) 场景覆盖薄弱；温度区间按该场景自己的请求统计，不拿全局数据凑。
    for scene, stats in sorted(scene_stats.items()):
        if stats["weak_ratio"] < WEAK_SCENE_RATIO:
            continue
        percent = round(stats["weak_ratio"] * 100)
        scene_temps = scene_temperatures.get(scene, [])
        temperature_hint = (
            f"（该场景请求记录的温度区间为 {min(scene_temps):.0f}~"
            f"{max(scene_temps):.0f}°C）"
            if scene_temps
            else ""
        )
        gaps.append(
            f"{scene} 场景近 {LOOKBACK_DAYS} 天共 {int(stats['requests'])} 次请求，"
            f"其中 {int(stats['weak_requests'])} 次可搭配套数少于 "
            f"{MIN_FEASIBLE_OUTFITS} 套（占 {percent}%），场景覆盖薄弱"
            f"{temperature_hint}，建议补充适配该场景的单品。"
        )

    # 3) 样本不足时仍给一条具体的、不夸大的基线描述（验收 4：至少 1 条）。
    if not gaps:
        category_summary = (
            "、".join(
                f"{name} {count} 件"
                for name, count in sorted(
                    owned_counts.items(), key=lambda item: (item[1], item[0])
                )[:3]
            )
            or "尚无 owned 单品"
        )
        gaps.append(
            f"近 {LOOKBACK_DAYS} 天只有 {len(sessions)} 次推荐记录，"
            f"未出现明显的结构性缺口（薄弱请求 ≥ "
            f"{int(WEAK_SCENE_RATIO * 100)}% 才计入）；"
            f"当前衣橱数量最少的品类是：{category_summary}，可优先观察这类单品的"
            f"实际使用频率，暂不建议据此下单。"
        )

    return {
        "lookback_days": LOOKBACK_DAYS,
        "sessions_considered": len(sessions),
        "owned_garment_count": sum(owned_counts.values()),
        "owned_by_category": dict(sorted(owned_counts.items())),
        "scene_stats": scene_stats,
        "temperature_range": (
            {"min": min(temperatures), "max": max(temperatures)}
            if temperatures
            else None
        ),
        "thresholds": {
            "weak_scene_ratio": WEAK_SCENE_RATIO,
            "min_feasible_outfits": MIN_FEASIBLE_OUTFITS,
            "min_match_score": MIN_MATCH_SCORE,
        },
        "gaps": gaps,
        "feasible_outfit_count_note": (
            "outfit_sessions 没有该列；用 recommendations 中 match_score>=6 的套数近似"
        ),
    }


async def build_gap_report(
    *,
    owner_id: UUID,
    wardrobe_scope: str,
    garments: list[GarmentSchema],
    sessions: list[OutfitSessionSchema],
) -> GapReportResponse:
    """流程 D 主入口：统计 → TEXT_PLANNER 总结 → 响应。"""
    statistics = compute_statistics(garments, sessions)
    summary = ""
    payload: dict[str, Any] = {
        "wardrobe_scope": wardrobe_scope,
        "statistics": statistics,
        "gaps": statistics["gaps"],
        "output_format": {"summary": "中文，2-4 句，只陈述统计支持的内容"},
    }
    try:
        raw = await call_text_planner(
            load_prompt("gap_summary"),
            payload,
            role=ModelRole.MULTIMODAL_STYLIST,
        )
        text = raw.get("summary") or raw.get("gap_report") or raw.get("text")
        if isinstance(text, str) and text.strip():
            summary = text.strip()
    except (PlannerConfigError, PlannerCallError) as exc:
        logger.warning("缺口总结模型不可用，改用统计描述：%s", exc)
    if not summary:
        summary = "".join(statistics["gaps"][:2])

    return GapReportResponse(
        owner_id=owner_id,
        wardrobe_scope=wardrobe_scope,
        statistics=statistics,
        gaps=list(statistics["gaps"]),
        summary=summary,
    )
