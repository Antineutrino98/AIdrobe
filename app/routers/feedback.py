"""M5 反馈写入 + 偏好查询/清空接口。

职责
----
- `POST /feedback`：写一条反馈，然后用 BackgroundTasks 在**响应结束后**
  触发偏好重写（不在请求内同步调用模型）。
- `GET /preference` / `GET /preference/history` / `POST /preference/clear`：
  让用户查看、回滚与清空偏好（M5 验收 6）。

为什么 owner_id 要反查
----------------------
M6 前端 `sendFeedback` 只发 `{session_id, outfit_index, signal, wear_state?,
free_text?}`，不含 owner_id。这里用 session_id 反查 outfit_sessions.owner_id，
避免前端伪造归属。找不到会话一律 404。

数据库访问只经 db_gateway 唯一句柄；本文件不写 SQL、不直接拼 LLM 请求。
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.db import ensure_database, get_session_factory
from app.repositories.postgres import (
    PostgresGarmentRepository,
    PostgresPreferenceEngineRepository,
    PostgresSessionRepository,
)
from app.schemas import (
    FeedbackCreate,
    FeedbackRequest,
    FeedbackSchema,
    FeedbackSignal,
    PreferenceHistorySchema,
    UserPreferenceSchema,
    WearState,
)
from app.services.preference_engine import (
    clear_preference,
    is_explicit_trigger,
    run_rewrite_in_background,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["feedback"])


async def _apply_worn_state(
    garments: PostgresGarmentRepository,
    session_row: Any,
    outfit_index: int,
    signal: FeedbackSignal,
    wear_state_after: WearState | None,
) -> int:
    """M7：signal=worn 时更新该套搭配里的单品穿着状态。

    范围只限「该套搭配里的单品」——用户反馈的是这一套，不是整个衣橱。
    `wear_state_after` 为空时仍记录穿着日期，但不改状态。
    返回实际更新的单品数。
    """
    if signal != FeedbackSignal.WORN:
        return 0
    recommendations = session_row.recommendations or []
    if not (0 <= outfit_index < len(recommendations)):
        return 0
    outfit = recommendations[outfit_index]
    if not isinstance(outfit, dict):
        return 0

    today: str = date.today().isoformat()
    updated: int = 0
    for item in outfit.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            garment_id = uuid.UUID(str(item.get("garment_id")))
        except (ValueError, TypeError):
            continue
        garment = await garments.get(garment_id)
        if garment is None or garment.owner_id != session_row.owner_id:
            continue
        if wear_state_after is not None:
            await garments.update_wear_state(garment_id, wear_state_after)
        await garments.append_wear_date(garment_id, today)
        updated += 1
    return updated


@router.post("/feedback", response_model=FeedbackSchema)
async def create_feedback(
    body: FeedbackRequest,
    background_tasks: BackgroundTasks,
) -> FeedbackSchema:
    """写入反馈；响应返回后再在后台重写偏好。"""
    await ensure_database()
    factory = get_session_factory()

    # 注意：会话在这里显式关闭（async with 退出），后台任务随后新开连接，
    # 不与本请求并发写同一连接（M5 验收 7）。
    async with factory() as session:
        engine_repo = PostgresPreferenceEngineRepository(session)
        sessions = PostgresSessionRepository(session)
        garments = PostgresGarmentRepository(session)

        session_row = await engine_repo.get_session(body.session_id)
        if session_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的推荐会话",
            )

        feedback = await sessions.record_feedback(
            FeedbackCreate(
                session_id=body.session_id,
                owner_id=session_row.owner_id,
                outfit_index=body.outfit_index,
                signal=body.signal,
                wear_state_after=body.wear_state,
                free_text=body.free_text,
            )
        )
        # M7：身份校验通过后，同步该套单品的穿着状态。
        try:
            changed = await _apply_worn_state(
                garments,
                session_row,
                body.outfit_index,
                body.signal,
                body.wear_state,
            )
            if changed:
                logger.info(
                    "反馈带动单品状态更新：session=%s outfit=%d count=%d state=%s",
                    body.session_id,
                    body.outfit_index,
                    changed,
                    body.wear_state,
                )
        except Exception as exc:  # noqa: BLE001 - 反馈已写入，状态更新失败不回滚
            logger.warning("更新单品穿着状态失败（反馈已写入）：%s", exc)

        owner_id: uuid.UUID = session_row.owner_id
        explicit: bool = is_explicit_trigger(body.free_text)
    background_tasks.add_task(
        run_rewrite_in_background,
        owner_id,
        explicit=explicit,
    )
    return feedback


@router.get("/preference", response_model=UserPreferenceSchema | None)
async def get_preference(owner_id: uuid.UUID) -> UserPreferenceSchema | None:
    """查看当前偏好；从未生成过时返回 null。"""
    await ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        repo = PostgresPreferenceEngineRepository(session)
        return await repo.get_preference_state(owner_id)


@router.get(
    "/preference/history", response_model=list[PreferenceHistorySchema]
)
async def get_preference_history(
    owner_id: uuid.UUID,
) -> list[PreferenceHistorySchema]:
    """查看最近 5 份历史版本（从新到旧）。"""
    await ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        repo = PostgresPreferenceEngineRepository(session)
        return await repo.get_history(owner_id)


@router.post("/preference/clear", response_model=UserPreferenceSchema)
async def clear_preference_endpoint(
    owner_id: uuid.UUID,
) -> UserPreferenceSchema:
    """清空偏好：当前文本置空、version 继续递增、历史保留。"""
    await ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        repo = PostgresPreferenceEngineRepository(session)
        result = await clear_preference(repo, owner_id)
    logger.info("偏好已清空：owner=%s version=%d", owner_id, result.version)
    return result
