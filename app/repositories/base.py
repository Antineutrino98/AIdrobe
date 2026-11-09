"""仓库层抽象接口（M1，新版 PGlite 契约）。

契约说明
--------
- 全部方法都是 async，符合 AIdrobe.md「所有数据库操作必须 async」。
- 只定义接口，不含任何 SQL、路由、业务逻辑或 LLM 调用。
- 实现见 app/repositories/postgres.py；M2/M3 只依赖这些抽象。
- 不使用 ORM relationship 做复杂查询（M1.md 明确要求）。
- 会话由外部注入；会话来自 db_gateway 的唯一引擎（单进程单连接），
  仓库层绝不自行打开 PGlite。
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

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


class GarmentRepository(ABC):
    """衣物数据访问接口。"""

    @abstractmethod
    async def create(self, data: GarmentCreate) -> GarmentSchema:
        """新增一件衣物。"""
        ...

    @abstractmethod
    async def get(self, garment_id: uuid.UUID) -> GarmentSchema | None:
        """按 id 取一件衣物；不存在返回 None。"""
        ...

    @abstractmethod
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
        """按 owner 列衣物；category/status/season 为可选过滤。

        season 按 JSONB 数组包含匹配，且 ["all"] 视为命中任何季节。
        """
        ...

    @abstractmethod
    async def update_embedding(
        self, garment_id: uuid.UUID, embedding: list[float]
    ) -> GarmentSchema | None:
        """写入/更新图像 embedding（预留给 M2 入库流程调用）。"""
        ...

    @abstractmethod
    async def update_wear_state(
        self, garment_id: uuid.UUID, wear_state: WearState
    ) -> GarmentSchema | None:
        """更新穿着状态（clean / worn_once / dirty / laundry）。"""
        ...

    @abstractmethod
    async def append_wear_date(
        self, garment_id: uuid.UUID, iso_date: str
    ) -> GarmentSchema | None:
        """追加一条穿着日期。

        recent_wear_dates 只保留最近 30 天；同时把 last_worn_at
        更新为该日期。iso_date 是 ISO 日期字符串（如 2026-11-05）。
        """
        ...

    async def list_all(self, *, limit: int = 1000) -> list[GarmentSchema]:
        """跨用户列衣物（M3 的 wardrobe_scope=all 使用）。

        非抽象方法：M1/M2 的内存实现可以不覆盖它；只有需要跨用户检索的
        PostgreSQL 实现覆盖。未覆盖时会抛 NotImplementedError。
        """
        raise NotImplementedError

    @abstractmethod
    async def find_similar_by_embedding(
        self,
        owner_id: uuid.UUID,
        embedding: list[float],
        threshold: float,
        limit: int = 10,
    ) -> list[tuple[uuid.UUID, float]]:
        """在同一 owner 的衣橱内按余弦相似度查重（全表扫描）。

        threshold 是「余弦相似度」0~1，越大越像（**不是距离**）；
        返回 (garment_id, similarity) 列表，按相似度从高到低排序。
        只对 embedding 非空的衣物计算；不依赖 GIN/HNSW 索引。
        """
        ...


class SessionRepository(ABC):
    """推荐会话与反馈的数据访问接口。"""

    @abstractmethod
    async def create_session(
        self, data: OutfitSessionCreate
    ) -> OutfitSessionSchema:
        """创建一次推荐请求会话；推荐结果由 M3 后续写回。"""
        ...

    @abstractmethod
    async def record_feedback(self, data: FeedbackCreate) -> FeedbackSchema:
        """记录一次针对第 outfit_index 套搭配的反馈。"""
        ...


class PreferenceRepository(ABC):
    """用户偏好文本快照与历史的数据访问接口。"""

    @abstractmethod
    async def get_preference_text(self, owner_id: uuid.UUID) -> str | None:
        """读取当前偏好文本；尚未生成时返回 None。"""
        ...

    @abstractmethod
    async def upsert_preference_text(
        self, data: PreferenceUpsert
    ) -> UserPreferenceSchema:
        """写入偏好文本；已存在时覆盖并把 version 加 1。"""
        ...

    @abstractmethod
    async def push_history(
        self, owner_id: uuid.UUID, preference_text: str, version: int
    ) -> PreferenceHistorySchema:
        """写入一条偏好历史，并只保留该用户最近 5 份。"""
        ...

    @abstractmethod
    async def get_history(
        self, owner_id: uuid.UUID,
    ) -> list[PreferenceHistorySchema]:
        """按 version 从新到旧返回该用户的偏好历史（最多 5 条）。"""
        ...


class PurchaseRepository(ABC):
    """M4 购前决策的数据访问接口。

    单独新建一个抽象类（而不是往 GarmentRepository / SessionRepository 里
    加抽象方法），是为了不破坏 M2 / M3 测试里那些内存假实现——那些类只
    实现了各自模块用到的方法。本模块同样不打开数据库、不写 SQL 到路由。
    """

    @abstractmethod
    async def create_evaluation(
        self, data: WishlistEvaluationCreate
    ) -> WishlistEvaluationSchema:
        """写一条购前决策记录（wishlist_evaluations 表）。"""
        ...

    @abstractmethod
    async def list_recent_sessions(
        self,
        owner_id: uuid.UUID | None,
        *,
        days: int = 30,
        limit: int = 500,
    ) -> list[OutfitSessionSchema]:
        """近 N 天的推荐会话；owner_id=None 表示跨用户（wardrobe_scope=all）。

        M4 流程 D 用它的 missing_slots / request_context.scene /
        recommendations[].match_score 做统计。
        """
        ...

    @abstractmethod
    async def get_user_settings(self, owner_id: uuid.UUID) -> dict[str, Any]:
        """读取 users.settings（M4 查重阈值与 strictness）；不存在返回 {}。"""
        ...


class PreferenceEngineRepository(ABC):
    """M5 偏好引擎的数据访问接口。

    单独新建抽象类（而不是往 PreferenceRepository 里加抽象方法），理由同
    PurchaseRepository：不破坏 M2/M3 测试里已有的内存假实现。
    本类负责「反馈 → 偏好」所需的读取与写入；偏好文本本身的 upsert /
    历史写入语义与 PostgresPreferenceRepository 保持一致（保留 5 份）。
    """

    @abstractmethod
    async def get_session(
        self, session_id: uuid.UUID
    ) -> Any | None:
        """按 id 取推荐会话（POST /feedback 用它反查 owner_id）。"""
        ...

    @abstractmethod
    async def get_preference_state(
        self, owner_id: uuid.UUID
    ) -> UserPreferenceSchema | None:
        """读取当前偏好快照（文本 + version + updated_at）；无则 None。"""
        ...

    @abstractmethod
    async def count_feedback(self, owner_id: uuid.UUID) -> int:
        """该用户的反馈总数（判断是否达到 5 条下限）。"""
        ...

    @abstractmethod
    async def count_feedback_since(
        self, owner_id: uuid.UUID, since: datetime | None
    ) -> int:
        """since 之后新增的反馈数；since=None 表示全部。"""
        ...

    @abstractmethod
    async def list_feedback_context(
        self, owner_id: uuid.UUID, *, limit: int = 30
    ) -> list[dict[str, Any]]:
        """最近 N 条反馈并关联 outfit_sessions（signal/会话上下文）。"""
        ...

    @abstractmethod
    async def get_garment_profiles(
        self, garment_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """批量取衣物画像（category/colors/formality/style_tags）。"""
        ...

    @abstractmethod
    async def upsert_preference_text(
        self, data: PreferenceUpsert
    ) -> UserPreferenceSchema:
        """写入偏好文本；已存在时 version 加 1。"""
        ...

    @abstractmethod
    async def push_history(
        self, owner_id: uuid.UUID, preference_text: str, version: int
    ) -> PreferenceHistorySchema:
        """写入历史版本，并只保留该用户最近 5 份。"""
        ...

    @abstractmethod
    async def get_history(
        self, owner_id: uuid.UUID
    ) -> list[PreferenceHistorySchema]:
        """按 version 从新到旧返回最近 5 份历史。"""
        ...


class UserRepository(ABC):
    """M7：用户列表（给 M6 顶部用户切换器用的只读接口）。"""

    @abstractmethod
    async def list_users(self, *, limit: int = 100) -> list[UserSchema]:
        """按创建时间从旧到新返回用户。"""
        ...
