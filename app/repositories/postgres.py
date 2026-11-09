"""仓库层的 PostgreSQL / PGlite 异步实现（M1）。

- 所有查询显式写在这里，不使用 ORM relationship（M1.md 要求）。
- 只做数据访问：不含硬过滤规则、推荐逻辑、LLM 调用（属 M2/M3）。
- 会话由外部注入，且必须来自 db_gateway 的唯一引擎；本层不打开数据库。

关于 find_similar_by_embedding（新版 M1.md）
-------------------------------------------
文档要求「用全表扫描计算余弦距离」，且 AIdrobe.md 禁止 GIN / 向量索引。
这里在 Python 侧对该 owner 下所有非空 embedding 逐条计算余弦相似度，
不依赖 `<=>` 操作符或任何索引：几百件衣物开销可忽略，
也避免 PGlite 的 pgvector comparator 与 SQLAlchemy 的兼容差异。
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EMBEDDING_DIM,
    Feedback,
    Garment,
    OutfitSession,
    User,
    UserPreference,
    UserPreferenceHistory,
    WishlistEvaluation,
)
from app.repositories.base import (
    GarmentRepository,
    PreferenceEngineRepository,
    PreferenceRepository,
    PurchaseRepository,
    SessionRepository,
    UserRepository,
)
from app.schemas import (
    FeedbackCreate,
    FeedbackSchema,
    GarmentCreate,
    GarmentSchema,
    GarmentStatus,
    OutfitSessionCreate,
    OutfitSessionSchema,
    PreferenceHistorySchema,
    PreferenceUpsert,
    UserPreferenceSchema,
    UserSchema,
    WearState,
    WishlistEvaluationCreate,
    WishlistEvaluationSchema,
)

HISTORY_KEEP: int = 5
WEAR_DATE_WINDOW_DAYS: int = 30


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量按 0.0 处理，避免除零。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class PostgresGarmentRepository(GarmentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def create(self, data: GarmentCreate) -> GarmentSchema:
        # 过滤 None：GarmentCreate.id 是可选的，显式传 None 会覆盖
        # 模型的 uuid4 默认值并导致主键为 NULL。
        payload: dict[str, Any] = {
            key: value
            for key, value in data.model_dump().items()
            if value is not None
        }
        # str 枚举显式转纯字符串，避免把枚举对象交给 String 列。
        payload["status"] = data.status.value
        payload["wear_state"] = data.wear_state.value
        obj = Garment(**payload)
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)  # 读取 server_default 生成的时间戳
        return GarmentSchema.model_validate(obj)

    async def get(self, garment_id: uuid.UUID) -> GarmentSchema | None:
        obj: Garment | None = await self._session.get(Garment, garment_id)
        if obj is None:
            return None
        return GarmentSchema.model_validate(obj)

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        *,
        category: str | None = None,
        status: GarmentStatus | None = None,
        season: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GarmentSchema]:
        stmt = select(Garment).where(Garment.owner_id == owner_id)
        if category is not None:
            stmt = stmt.where(Garment.category == category)
        if status is not None:
            stmt = stmt.where(Garment.status == status.value)
        if season is not None:
            # JSONB 数组包含匹配；["all"] 表示四季通穿，命中任何季节。
            stmt = stmt.where(
                or_(
                    Garment.seasons.contains([season]),
                    Garment.seasons.contains(["all"]),
                )
            )
        stmt = stmt.order_by(Garment.created_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [GarmentSchema.model_validate(row) for row in rows]

    async def update_embedding(
        self, garment_id: uuid.UUID, embedding: list[float]
    ) -> GarmentSchema | None:
        obj: Garment | None = await self._session.get(Garment, garment_id)
        if obj is None:
            return None
        obj.embedding = embedding
        await self._session.commit()
        await self._session.refresh(obj)
        return GarmentSchema.model_validate(obj)

    async def update_wear_state(
        self, garment_id: uuid.UUID, wear_state: WearState
    ) -> GarmentSchema | None:
        obj: Garment | None = await self._session.get(Garment, garment_id)
        if obj is None:
            return None
        obj.wear_state = WearState(wear_state).value
        await self._session.commit()
        await self._session.refresh(obj)
        return GarmentSchema.model_validate(obj)

    async def append_wear_date(
        self, garment_id: uuid.UUID, iso_date: str
    ) -> GarmentSchema | None:
        obj: Garment | None = await self._session.get(Garment, garment_id)
        if obj is None:
            return None
        cutoff: str = (
            date.today() - timedelta(days=WEAR_DATE_WINDOW_DAYS)
        ).isoformat()
        dates = sorted(
            {d for d in list(obj.recent_wear_dates or []) + [iso_date] if d >= cutoff}
        )
        obj.recent_wear_dates = dates
        parsed = datetime.fromisoformat(iso_date)
        obj.last_worn_at = (
            parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        )
        await self._session.commit()
        await self._session.refresh(obj)
        return GarmentSchema.model_validate(obj)

    async def list_all(self, *, limit: int = 1000) -> list[GarmentSchema]:
        """跨用户列衣物；M3 的 wardrobe_scope=all / shared 降级使用。"""
        stmt = select(Garment).order_by(Garment.created_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [GarmentSchema.model_validate(row) for row in rows]

    async def find_similar_by_embedding(
        self,
        owner_id: uuid.UUID,
        embedding: list[float],
        threshold: float,
        limit: int = 10,
    ) -> list[tuple[uuid.UUID, float]]:
        """全表扫描计算余弦相似度，按从高到低返回 (garment_id, similarity)。"""
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding 维度必须是 {EMBEDDING_DIM}，实际为 {len(embedding)}"
            )
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold 是余弦相似度，必须在 0.0 ~ 1.0 之间")

        stmt = select(Garment.id, Garment.embedding).where(
            Garment.owner_id == owner_id,
            Garment.embedding.is_not(None),
        )
        rows = (await self._session.execute(stmt)).all()
        scored: list[tuple[uuid.UUID, float]] = []
        for row in rows:
            vector = list(row.embedding) if row.embedding is not None else []
            if len(vector) != EMBEDDING_DIM:
                continue
            score = _cosine_similarity(embedding, vector)
            if score >= threshold:
                scored.append((row.id, float(score)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


class PostgresSessionRepository(SessionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def create_session(
        self, data: OutfitSessionCreate
    ) -> OutfitSessionSchema:
        obj = OutfitSession(
            owner_id=data.owner_id,
            request_context=data.request_context,
            layering_plan=data.layering_plan,
            recommendations=data.recommendations,
            missing_slots=data.missing_slots,
            token_usage=data.token_usage,
            model_used=data.model_used,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return OutfitSessionSchema.model_validate(obj)

    async def record_feedback(self, data: FeedbackCreate) -> FeedbackSchema:
        obj = Feedback(
            session_id=data.session_id,
            owner_id=data.owner_id,
            outfit_index=data.outfit_index,
            signal=data.signal.value,
            wear_state_after=(
                data.wear_state_after.value if data.wear_state_after else None
            ),
            free_text=data.free_text,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return FeedbackSchema.model_validate(obj)


class PostgresPreferenceRepository(PreferenceRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def get_preference_text(self, owner_id: uuid.UUID) -> str | None:
        obj: UserPreference | None = await self._session.get(
            UserPreference, owner_id
        )
        if obj is None:
            return None
        return obj.preference_text

    async def get_user_settings(self, owner_id: uuid.UUID) -> dict[str, Any]:
        """读取 users.settings（推荐策略等）；用户不存在返回空字典。"""
        obj: User | None = await self._session.get(User, owner_id)
        return dict(obj.settings or {}) if obj is not None else {}

    async def upsert_preference_text(
        self, data: PreferenceUpsert
    ) -> UserPreferenceSchema:
        obj: UserPreference | None = await self._session.get(
            UserPreference, data.owner_id
        )
        if obj is None:
            obj = UserPreference(
                owner_id=data.owner_id,
                preference_text=data.preference_text,
                version=1,
            )
            self._session.add(obj)
        else:
            obj.preference_text = data.preference_text
            obj.version = obj.version + 1  # version 自增在本层完成
        await self._session.commit()
        await self._session.refresh(obj)
        return UserPreferenceSchema.model_validate(obj)

    async def push_history(
        self, owner_id: uuid.UUID, preference_text: str, version: int
    ) -> PreferenceHistorySchema:
        entry = UserPreferenceHistory(
            owner_id=owner_id,
            preference_text=preference_text,
            version=version,
        )
        self._session.add(entry)
        await self._session.flush()

        # 只保留该用户最近 5 份：按 version 从新到旧，跳过前 5 条后删除其余。
        stale_ids = (
            await self._session.execute(
                select(UserPreferenceHistory.id)
                .where(UserPreferenceHistory.owner_id == owner_id)
                .order_by(
                    UserPreferenceHistory.version.desc(),
                    UserPreferenceHistory.created_at.desc(),
                )
                .offset(HISTORY_KEEP)
            )
        ).scalars().all()
        if stale_ids:
            await self._session.execute(
                delete(UserPreferenceHistory).where(
                    UserPreferenceHistory.id.in_(list(stale_ids))
                )
            )
        await self._session.commit()
        await self._session.refresh(entry)
        return PreferenceHistorySchema.model_validate(entry)

    async def get_history(
        self, owner_id: uuid.UUID,
    ) -> list[PreferenceHistorySchema]:
        rows = (
            await self._session.execute(
                select(UserPreferenceHistory)
                .where(UserPreferenceHistory.owner_id == owner_id)
                .order_by(UserPreferenceHistory.version.desc())
                .limit(HISTORY_KEEP)
            )
        ).scalars().all()
        return [PreferenceHistorySchema.model_validate(row) for row in rows]


class PostgresPurchaseRepository(PurchaseRepository):
    """M4：购前决策记录写入 + 近 30 天会话查询（只做数据访问）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def create_evaluation(
        self, data: WishlistEvaluationCreate
    ) -> WishlistEvaluationSchema:
        obj = WishlistEvaluation(
            owner_id=data.owner_id,
            candidate_garment_id=data.candidate_garment_id,
            existing_similar_ids=list(data.existing_similar_ids),
            feasible_outfit_count=data.feasible_outfit_count,
            scene_coverage=list(data.scene_coverage),
            llm_verdict=data.llm_verdict,
            llm_reasoning=data.llm_reasoning,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return WishlistEvaluationSchema.model_validate(obj)

    async def list_recent_sessions(
        self,
        owner_id: uuid.UUID | None,
        *,
        days: int = 30,
        limit: int = 500,
    ) -> list[OutfitSessionSchema]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = select(OutfitSession).where(OutfitSession.created_at >= cutoff)
        if owner_id is not None:
            stmt = stmt.where(OutfitSession.owner_id == owner_id)
        stmt = stmt.order_by(OutfitSession.created_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [OutfitSessionSchema.model_validate(row) for row in rows]

    async def get_user_settings(self, owner_id: uuid.UUID) -> dict[str, Any]:
        obj: User | None = await self._session.get(User, owner_id)
        if obj is None:
            return {}
        return dict(obj.settings or {})


class PostgresPreferenceEngineRepository(PreferenceEngineRepository):
    """M5：反馈上下文读取 + 偏好快照/历史写入（只做数据访问）。

    偏好 upsert / 历史保留 5 份的语义与 PostgresPreferenceRepository 完全一致；
    这里重复实现是为了让偏好引擎只依赖一个接口，同时不动 M1 已冻结的
    PreferenceRepository 及其测试假实现。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def get_session(
        self, session_id: uuid.UUID
    ) -> OutfitSessionSchema | None:
        obj: OutfitSession | None = await self._session.get(
            OutfitSession, session_id
        )
        if obj is None:
            return None
        return OutfitSessionSchema.model_validate(obj)

    async def get_preference_state(
        self, owner_id: uuid.UUID
    ) -> UserPreferenceSchema | None:
        obj: UserPreference | None = await self._session.get(
            UserPreference, owner_id
        )
        if obj is None:
            return None
        return UserPreferenceSchema.model_validate(obj)

    async def count_feedback(self, owner_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.owner_id == owner_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_feedback_since(
        self, owner_id: uuid.UUID, since: datetime | None
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.owner_id == owner_id)
        )
        if since is not None:
            stmt = stmt.where(Feedback.created_at > since)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_feedback_context(
        self, owner_id: uuid.UUID, *, limit: int = 30
    ) -> list[dict[str, Any]]:
        stmt = (
            select(Feedback, OutfitSession)
            .join(OutfitSession, Feedback.session_id == OutfitSession.id)
            .where(Feedback.owner_id == owner_id)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "signal": feedback.signal,
                "wear_state_after": feedback.wear_state_after,
                "free_text": feedback.free_text,
                "outfit_index": feedback.outfit_index,
                "created_at": feedback.created_at,
                "session": {
                    "request_context": session.request_context,
                    "recommendations": session.recommendations,
                },
            }
            for feedback, session in rows
        ]

    async def get_garment_profiles(
        self, garment_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        if not garment_ids:
            return {}
        stmt = select(Garment).where(Garment.id.in_(list(garment_ids)))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            row.id: {
                "category": row.category,
                "subcategory": row.subcategory,
                "colors": row.colors,
                "formality": row.formality,
                "style_tags": row.style_tags,
            }
            for row in rows
        }

    async def upsert_preference_text(
        self, data: PreferenceUpsert
    ) -> UserPreferenceSchema:
        obj: UserPreference | None = await self._session.get(
            UserPreference, data.owner_id
        )
        if obj is None:
            obj = UserPreference(
                owner_id=data.owner_id,
                preference_text=data.preference_text,
                version=1,
            )
            self._session.add(obj)
        else:
            obj.preference_text = data.preference_text
            obj.version = obj.version + 1
        await self._session.commit()
        await self._session.refresh(obj)
        return UserPreferenceSchema.model_validate(obj)

    async def push_history(
        self, owner_id: uuid.UUID, preference_text: str, version: int
    ) -> PreferenceHistorySchema:
        entry = UserPreferenceHistory(
            owner_id=owner_id,
            preference_text=preference_text,
            version=version,
        )
        self._session.add(entry)
        await self._session.flush()

        stale_ids = (
            await self._session.execute(
                select(UserPreferenceHistory.id)
                .where(UserPreferenceHistory.owner_id == owner_id)
                .order_by(
                    UserPreferenceHistory.version.desc(),
                    UserPreferenceHistory.created_at.desc(),
                )
                .offset(HISTORY_KEEP)
            )
        ).scalars().all()
        if stale_ids:
            await self._session.execute(
                delete(UserPreferenceHistory).where(
                    UserPreferenceHistory.id.in_(list(stale_ids))
                )
            )
        await self._session.commit()
        await self._session.refresh(entry)
        return PreferenceHistorySchema.model_validate(entry)

    async def get_history(
        self, owner_id: uuid.UUID
    ) -> list[PreferenceHistorySchema]:
        rows = (
            await self._session.execute(
                select(UserPreferenceHistory)
                .where(UserPreferenceHistory.owner_id == owner_id)
                .order_by(UserPreferenceHistory.version.desc())
                .limit(HISTORY_KEEP)
            )
        ).scalars().all()
        return [PreferenceHistorySchema.model_validate(row) for row in rows]


class PostgresUserRepository(UserRepository):
    """M7：用户列表（只读，给 M6 顶部切换器）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def release(self) -> None:
        """把数据库连接还回连接池。

        PGlite 只有一条连接。模型调用动辄几十秒，如果读数据后一直占着
        连接不放，期间所有 /users、/garments 请求都会等满 30 秒然后 500。
        所以「读完数据 → 先归还连接 → 再调模型」是必须的。
        """
        await self._session.commit()

    async def list_users(self, *, limit: int = 100) -> list[UserSchema]:
        stmt = select(User).order_by(User.created_at.asc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [UserSchema.model_validate(row) for row in rows]
