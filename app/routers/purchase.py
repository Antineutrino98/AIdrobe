"""M4 购前决策接口：POST /evaluate-purchase、POST /gap-report。

编排职责（不在路由里写 SQL、不直接拼 LLM 请求）
------------------------------------------------
- 候选单品与衣橱通过 M1 仓库层读取；数据库只经 db_gateway 的唯一句柄。
- 流程 A/B/C 交给 services/purchase_decision.evaluate_candidate()。
- 流程 D 交给 services/gap_analysis.build_gap_report()。
- 评估结果写回 M1 已有的 wishlist_evaluations 表；**不改表、不加迁移**。

请求字段兼容 M4.md 的 user_id/candidate_garment_id 与 M6 前端的
owner_id/garment_id（见 app/schemas.py 的 AliasChoices 说明）。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.db import ensure_database, get_session_factory
from app.repositories.postgres import (
    PostgresGarmentRepository,
    PostgresPreferenceRepository,
    PostgresPurchaseRepository,
)
from app.schemas import (
    EvaluatePurchaseRequest,
    GapReportRequest,
    GapReportResponse,
    PurchaseEvaluationResponse,
    WishlistEvaluationCreate,
)
from app.services.gap_analysis import build_gap_report
from app.services.purchase_decision import evaluate_candidate

logger: logging.Logger = logging.getLogger(__name__)

WARDROBE_LIMIT: int = 1000
LOOKBACK_DAYS: int = 30

router: APIRouter = APIRouter(tags=["purchase"])


class PurchaseService:
    """两个接口共用的编排服务（每个请求一个 AsyncSession）。"""

    def __init__(
        self,
        garments: PostgresGarmentRepository,
        preferences: PostgresPreferenceRepository,
        purchases: PostgresPurchaseRepository,
    ) -> None:
        self._garments = garments
        self._preferences = preferences
        self._purchases = purchases

    async def _load_wardrobe(
        self, owner_id: uuid.UUID, scope: str
    ) -> list[Any]:
        if scope == "mine":
            return await self._garments.list_by_owner(
                owner_id, limit=WARDROBE_LIMIT
            )
        if scope == "shared":
            # 与 M3 一致：M1 没有家庭/伴侣关系表，无法判定共享成员。
            logger.warning(
                "wardrobe_scope=shared 需要家庭关系表（M1 未提供），已退化为 all"
            )
        return await self._garments.list_all(limit=WARDROBE_LIMIT)

    async def evaluate(
        self, request: EvaluatePurchaseRequest
    ) -> PurchaseEvaluationResponse:
        candidate = await self._garments.get(request.candidate_garment_id)
        if candidate is None or candidate.owner_id != request.owner_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到这件候选单品，或它不属于当前用户",
            )

        wardrobe = await self._load_wardrobe(request.owner_id, "mine")
        settings = await self._purchases.get_user_settings(request.owner_id)
        preference = await self._preferences.get_preference_text(request.owner_id)
        # 数据已读进内存，先归还唯一的数据库连接再调模型。
        release = getattr(self._garments, "release", None)
        if release is not None:
            await release()

        bundle = await evaluate_candidate(
            candidate=candidate,
            wardrobe=wardrobe,
            garments=self._garments,
            settings=settings,
            strictness_override=request.strictness,
            preference_text=preference,
        )
        decision = bundle.decision
        duplicate = bundle.duplicate

        # 写回 M1 已有的 wishlist_evaluations 表。写失败不吞掉评估结果：
        # 用户仍然能看到结论，只在日志里留下告警。
        evaluation = None
        try:
            evaluation = await self._purchases.create_evaluation(
                WishlistEvaluationCreate(
                    owner_id=request.owner_id,
                    candidate_garment_id=request.candidate_garment_id,
                    existing_similar_ids=duplicate.existing_similar_ids,
                    feasible_outfit_count=decision.feasible_outfit_count,
                    scene_coverage=decision.scene_coverage,
                    llm_verdict=decision.verdict,
                    llm_reasoning=decision.reasoning,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 审计写入失败不影响主流程
            logger.warning("购前评估记录写入失败（响应仍返回）：%s", exc)

        logger.info(
            "评估完成：candidate=%s verdict=%s feasible=%d duplicate=%s/%s",
            request.candidate_garment_id,
            decision.verdict,
            decision.feasible_outfit_count,
            duplicate.check,
            decision.duplicate_risk,
        )
        return PurchaseEvaluationResponse(
            id=evaluation.id if evaluation else None,
            owner_id=request.owner_id,
            candidate_garment_id=request.candidate_garment_id,
            verdict=decision.verdict,
            llm_verdict=decision.verdict,
            reasoning=decision.reasoning,
            llm_reasoning=decision.reasoning,
            duplicate_check=duplicate.check,
            duplicate_risk=decision.duplicate_risk,
            existing_similar_ids=duplicate.existing_similar_ids,
            similar_items=duplicate.similar_items,
            feasible_outfit_count=decision.feasible_outfit_count,
            scene_coverage=decision.scene_coverage,
            created_at=evaluation.created_at if evaluation else None,
        )

    async def gap_report(self, request: GapReportRequest) -> GapReportResponse:
        wardrobe = await self._load_wardrobe(
            request.owner_id, request.wardrobe_scope
        )
        # all / shared 需要跨用户会话统计；mine 只看本人。
        session_owner = (
            request.owner_id if request.wardrobe_scope == "mine" else None
        )
        sessions = await self._purchases.list_recent_sessions(
            session_owner, days=LOOKBACK_DAYS
        )
        # 统计所需数据齐了，先归还连接（缺口总结也要调模型）。
        release = getattr(self._purchases, "release", None)
        if release is not None:
            await release()
        return await build_gap_report(
            owner_id=request.owner_id,
            wardrobe_scope=request.wardrobe_scope,
            garments=wardrobe,
            sessions=sessions,
        )


async def get_purchase_service() -> AsyncIterator[PurchaseService]:
    """每个请求一个 AsyncSession；数据库由 db_gateway 唯一句柄提供。"""
    await ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        yield PurchaseService(
            PostgresGarmentRepository(session),
            PostgresPreferenceRepository(session),
            PostgresPurchaseRepository(session),
        )


@router.post("/evaluate-purchase", response_model=PurchaseEvaluationResponse)
async def evaluate_purchase(
    body: EvaluatePurchaseRequest,
    service: PurchaseService = Depends(get_purchase_service),
) -> PurchaseEvaluationResponse:
    return await service.evaluate(body)


@router.post("/gap-report", response_model=GapReportResponse)
async def gap_report(
    body: GapReportRequest,
    service: PurchaseService = Depends(get_purchase_service),
) -> GapReportResponse:
    return await service.gap_report(body)
