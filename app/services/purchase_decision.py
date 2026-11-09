"""M4 流程 B（搭配验证）+ 流程 C（综合决策）。

流程 B：把候选单品**只在内存里**注入衣橱，直接复用 U1 重构后的
`plan_retrieval` / `build_global_pool` / `rank_outfits`，
统计「包含这件候选、且 match_score ≥ 6」的搭配数量与覆盖场景。

与 M3 的三点差异
----------------
1. U1 之后没有槽位、也没有按 attributes 的分桶条件；候选单品和整份衣橱
   一起进同一个混合池，由搭配模型判断它能不能融入自由组合。
2. candidate 的 wear_state 视为 clean；同时因为池子只认 `status='owned'`，
   内存副本也改成 owned。**不写数据库**。
3. 只认包含 candidate 的搭配，累计上限 8 套（M4「最多生成 8 套」）。

流程 C：查重结果 + 可搭配套数 + strictness + 偏好文本 → TEXT_PLANNER。
代码统计值（套数 / 场景 / 重复风险）是硬事实，LLM 不能改写；
模型不可用时用规则给出确定性的中文结论，不让接口直接崩掉。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.llm_config import ModelRole
from app.prompts import load_prompt
from app.schemas import GarmentSchema, GarmentStatus, WearState
from app.services.duplicate_check import (
    DuplicateResult,
    PlannerCallError,
    PlannerConfigError,
    SIMILAR_LIMIT,
    call_text_planner,
    ensure_candidate_embedding,
    garment_profile,
    resolve_strictness,
    resolve_threshold,
    run_duplicate_check,
)
from app.services.filter import (
    POOL_LIMIT,
    build_global_pool,
    is_recommendable,
)
from app.services.retrieval_planner import (
    PlannerConfigError as RetrievalConfigError,
    PlannerError,
    RetrievalPlan,
    DEFAULT_PLAN,
    plan_retrieval,
)
from app.services.stylist_llm import (
    StylistConfigError,
    StylistError,
    rank_outfits,
)

logger: logging.Logger = logging.getLogger(__name__)

# 评估用的场景集合。M4.md 没给清单，取 M3 已冻结的 scene 里最常见的三个，
# 既能覆盖「场景覆盖」语义，也避免为每个场景各调两次模型。
EVALUATION_SCENES: tuple[str, ...] = ("work", "weekend", "date")
MAX_FEASIBLE_OUTFITS: int = 8
MIN_MATCH_SCORE: int = 6
VERDICTS: frozenset[str] = frozenset({"buy", "skip", "think_again"})
VERDICT_ZH: dict[str, str] = {
    "buy": "建议购买",
    "skip": "建议跳过",
    "think_again": "建议再考虑",
}


def virtual_candidate(candidate: GarmentSchema) -> GarmentSchema:
    """M4 流程 B：内存副本，owned + clean，绝不落库。"""
    return candidate.model_copy(
        update={"status": GarmentStatus.OWNED, "wear_state": WearState.CLEAN}
    )


def wardrobe_counts(garments: list[GarmentSchema]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for garment in garments:
        if is_recommendable(garment):
            counts[garment.category] = counts.get(garment.category, 0) + 1
    return counts


def fills_category_gap(
    candidate: GarmentSchema, wardrobe: list[GarmentSchema]
) -> bool:
    """衣橱里没有同 category 的 owned 单品 = 填补品类空白。"""
    for garment in wardrobe:
        if garment.id == candidate.id or garment.status != GarmentStatus.OWNED:
            continue
        if garment.category == candidate.category:
            return False
    return True


def default_plan() -> RetrievalPlan:
    """planner 不可用时，用最宽的全局区间，让整份衣橱都进池子。"""
    return DEFAULT_PLAN.model_copy(deep=True)


@dataclass
class FeasibilityResult:
    feasible_outfit_count: int = 0
    scene_coverage: list[str] = field(default_factory=list)
    feasible: bool = False
    per_scene: dict[str, int] = field(default_factory=dict)
    model_used: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class DecisionResult:
    verdict: str
    reasoning: str
    duplicate_risk: str
    feasible_outfit_count: int
    scene_coverage: list[str]
    fills_gap: bool


@dataclass
class EvaluationBundle:
    duplicate: DuplicateResult
    feasibility: FeasibilityResult
    decision: DecisionResult
    strictness: str
    threshold: float


async def verify_outfit_feasibility(
    *,
    candidate: GarmentSchema,
    wardrobe: list[GarmentSchema],
    preference_text: str | None,
) -> FeasibilityResult:
    """流程 B：多个场景下复用 M3 的 Step 2/3/4，统计合格搭配数。"""
    result = FeasibilityResult()
    injected = virtual_candidate(candidate)
    garments = [injected] + [item for item in wardrobe if item.id != candidate.id]
    counts = wardrobe_counts(garments)

    for scene in EVALUATION_SCENES:
        if result.feasible_outfit_count >= MAX_FEASIBLE_OUTFITS:
            break
        try:
            plan = await plan_retrieval(
                scene=scene,
                free_text=None,
                temperature=None,
                weather_condition=None,
                preference_text=preference_text,
                category_counts=counts,
            )
        except (RetrievalConfigError, PlannerError) as exc:
            logger.warning("场景 %s 的检索计划不可用，改用默认计划：%s", scene, exc)
            result.notes.append(f"{scene}: 计划降级")
            plan = default_plan()

        pool = build_global_pool(garments, plan, limit=POOL_LIMIT)
        for note in pool.notes:
            result.notes.append(f"{scene}: {note}")

        # 候选必须进池子且排在首位，确保一定被传给搭配模型并拿到图。
        others = [item for item in pool.items if item.id != candidate.id]
        pool.items = [injected] + others[: max(0, POOL_LIMIT - 1)]

        try:
            ranked = await rank_outfits(
                pool=pool,
                scene=scene,
                temperature=None,
                weather_condition=None,
                required_garment_id=candidate.id,
                # M5：M4 流程 B 复用 M3，自动继承偏好注入。
                preference_text=preference_text,
                plan=plan,
            )
        except (StylistConfigError, StylistError) as exc:
            logger.warning("场景 %s 的搭配验证不可用：%s", scene, exc)
            result.notes.append(f"{scene}: 搭配模型不可用")
            result.per_scene[scene] = 0
            continue

        good = [
            outfit
            for outfit in ranked.recommendations
            if outfit.match_score >= MIN_MATCH_SCORE
        ]
        remaining = MAX_FEASIBLE_OUTFITS - result.feasible_outfit_count
        take = min(len(good), remaining)
        result.per_scene[scene] = take
        if take > 0:
            result.feasible_outfit_count += take
            result.scene_coverage.append(scene)
        if ranked.model_used:
            result.model_used = ranked.model_used

    result.feasible = result.feasible_outfit_count > 0
    return result


def rule_verdict(
    *,
    feasible_outfit_count: int,
    duplicate_risk: str,
    fills_gap: bool,
    strictness: str,
) -> str:
    """M4 流程 C 的规则兜底（与 prompt 里的 Logic 逐条对应）。"""
    if feasible_outfit_count == 0:
        return "skip"
    if duplicate_risk == "high":
        return "skip"
    if feasible_outfit_count >= 3 and (fills_gap or strictness == "permissive"):
        return "buy"
    return "think_again"


def fallback_reasoning(
    *,
    candidate: GarmentSchema,
    decision: str,
    duplicate: DuplicateResult,
    feasibility: FeasibilityResult,
    fills_gap: bool,
) -> str:
    risk_zh = {"high": "高", "medium": "中", "low": "低", "none": "无"}
    similar = len(duplicate.matches)
    gap_text = "属于衣橱里还没有的品类" if fills_gap else "与衣橱现有品类重叠"
    scenes = "、".join(feasibility.scene_coverage) or "暂无明确场景"
    return (
        f"这件{candidate.category}类单品与衣橱中 {similar} 件已有衣物相似，"
        f"重复风险为{risk_zh.get(duplicate.risk, '未知')}，{gap_text}。"
        f"按当前衣橱试搭，共有 {feasibility.feasible_outfit_count} 套评分不低于 "
        f"{MIN_MATCH_SCORE} 分的搭配，覆盖场景：{scenes}。"
        f"综合以上统计，{VERDICT_ZH.get(decision, '建议再考虑')}。"
    )


async def decide(
    *,
    candidate: GarmentSchema,
    duplicate: DuplicateResult,
    feasibility: FeasibilityResult,
    wardrobe: list[GarmentSchema],
    preference_text: str | None,
    strictness: str,
) -> DecisionResult:
    """流程 C：TEXT_PLANNER 给结论，代码用统计值做硬校正。"""
    fills_gap = fills_category_gap(candidate, wardrobe)
    fallback = rule_verdict(
        feasible_outfit_count=feasibility.feasible_outfit_count,
        duplicate_risk=duplicate.risk,
        fills_gap=fills_gap,
        strictness=strictness,
    )
    verdict = fallback
    reasoning = ""

    # M4.md「User」段点名的六项内容，全部随请求发给模型。
    payload: dict[str, Any] = {
        "candidate": garment_profile(candidate),
        "matches": duplicate.matches,
        "duplicate_check": duplicate.check,
        "duplicate_risk": duplicate.risk,
        "feasible_outfit_count": feasibility.feasible_outfit_count,
        "scene_coverage": feasibility.scene_coverage,
        "strictness": strictness,
        "fills_gap": fills_gap,
        "user_preference": preference_text or "",
    }
    try:
        raw = await call_text_planner(
            load_prompt("purchase_decision"),
            payload,
            role=ModelRole.MULTIMODAL_STYLIST,
        )
        proposed = raw.get("verdict")
        if proposed in VERDICTS:
            verdict = proposed
        proposed_reasoning = raw.get("reasoning")
        if isinstance(proposed_reasoning, str) and proposed_reasoning.strip():
            reasoning = proposed_reasoning.strip()
    except (PlannerConfigError, PlannerCallError) as exc:
        logger.warning("综合决策模型不可用，改用规则结论：%s", exc)

    # 统计事实优先于模型的措辞：0 套或高重复风险一定是 skip。
    if feasibility.feasible_outfit_count == 0 or duplicate.risk == "high":
        verdict = "skip"
    if not reasoning:
        reasoning = fallback_reasoning(
            candidate=candidate,
            decision=verdict,
            duplicate=duplicate,
            feasibility=feasibility,
            fills_gap=fills_gap,
        )
    return DecisionResult(
        verdict=verdict,
        reasoning=reasoning,
        duplicate_risk=duplicate.risk,
        feasible_outfit_count=feasibility.feasible_outfit_count,
        scene_coverage=list(feasibility.scene_coverage),
        fills_gap=fills_gap,
    )


async def evaluate_candidate(
    *,
    candidate: GarmentSchema,
    wardrobe: list[GarmentSchema],
    garments: Any,
    settings: dict[str, Any] | None,
    strictness_override: str | None,
    preference_text: str | None,
) -> EvaluationBundle:
    """流程 A → B → C 的编排入口；路由只负责取参数、落库和组响应。"""
    strictness = resolve_strictness(settings, strictness_override)
    threshold = resolve_threshold(settings, strictness)

    resolved = await ensure_candidate_embedding(candidate, garments)
    if resolved is None:
        duplicate = DuplicateResult(
            check="unavailable", threshold=threshold, strictness=strictness
        )
        effective = candidate
    else:
        effective = resolved
        finder = getattr(garments, "find_similar_by_embedding", None)
        similar: list[tuple[uuid.UUID, float]] = []
        if finder is not None:
            try:
                similar = await finder(
                    effective.owner_id,
                    effective.embedding or [],
                    threshold,
                    SIMILAR_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 - 查重失败不阻断搭配验证
                logger.warning("向量查重失败，按无重复处理：%s", exc)
        # 候选自身可能在库里（M6 前端先 /ingest 再评估），必须排除自比。
        similar = [
            (garment_id, score)
            for garment_id, score in similar
            if garment_id != effective.id
        ]
        duplicate = await run_duplicate_check(
            candidate=effective,
            similar=similar,
            garments=garments,
            settings=settings,
            strictness=strictness,
            threshold=threshold,
        )

    feasibility = await verify_outfit_feasibility(
        candidate=effective,
        wardrobe=wardrobe,
        preference_text=preference_text,
    )
    decision = await decide(
        candidate=effective,
        duplicate=duplicate,
        feasibility=feasibility,
        wardrobe=wardrobe,
        preference_text=preference_text,
        strictness=strictness,
    )
    return EvaluationBundle(
        duplicate=duplicate,
        feasibility=feasibility,
        decision=decision,
        strictness=strictness,
        threshold=threshold,
    )
