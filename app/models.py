"""SQLAlchemy 2.x 异步 ORM 模型（M1 交付物：models.py）。

7 张表：users / garments / outfit_sessions / feedback /
        user_preferences / user_preferences_history / wishlist_evaluations

依据：新版 AIdrobe.md + 新版 M1.md（2026-11-05 改版，PGlite 版）。

冻结决定（用户 2026-11-05 拍板）
--------------------------------
- **所有业务表的外键统一叫 owner_id**（含原来的 user_id）。
- **只建普通 B-Tree 索引，且仅 garments(owner_id, category, status) 一个**；
  不建 GIN、不建 HNSW / ivfflat 等向量索引（几百件衣物全表扫描即可）。
- category 仍是自由文本 text 列；48 类清单放在
  app/attribute_dimensions.py 的 CATEGORY_VALUES（M2/M3 共享契约），
  数据库层不冻结取值，也不加 CHECK。
- M2 曾引入的 description / category_confidence 两列不在新版表定义中，
  已随 0002 迁移一并移除；M1 schema 以本文件为准。

预留字段说明（只为后续模块留位置，本模块不写任何 M2/M3 逻辑）
------------------------------------------------------------
- garments.embedding / vision_raw：M2 入库时写入视觉结果与原始返回。
- layering_plan / recommendations / missing_slots / model_used：
  M3 的检索计划、搭配结果与审计信息；具体 JSON 形状由 M3 契约冻结，
  这里用 Any 做容器类型，不猜键名。
- wishlist_evaluations.*：后续购前决策使用。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 图像 embedding 维度。M1.md 固定为 vector(512)；PGlite 的 pgvector 扩展支持。
EMBEDDING_DIM: int = 512

# 枚举用「字符串列 + CHECK 约束」实现，不用 PostgreSQL 原生 ENUM，
# 以后加值不必 ALTER TYPE，迁移更好写。
GARMENT_STATUS_VALUES: tuple[str, ...] = ("owned", "wishlist", "archived")
FEEDBACK_SIGNAL_VALUES: tuple[str, ...] = (
    "liked",
    "disliked",
    "worn",
    "not_worn",
    "regenerated",
)
WEAR_STATE_VALUES: tuple[str, ...] = ("clean", "worn_once", "dirty", "laundry")


def _uuid_pk() -> Mapped[uuid.UUID]:
    """UUID 主键：由 Python 侧 uuid4 生成（不依赖数据库扩展）。"""
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    """带时区时间戳，由数据库 now() 填默认值。"""
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _updated_at() -> Mapped[datetime]:
    """updated_at 由 SQLAlchemy onupdate 维护，不加数据库触发器。"""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(Base):
    """用户（家庭/伴侣共用场景下的每个成员）。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    # settings(JSONB)：用户级配置；具体键名由后续模块契约冻结，M1 不猜形状。
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Garment(Base):
    """每件衣物一行。owner_id 必填，支持多用户独立衣橱。"""

    __tablename__ = "garments"

    __table_args__: tuple[Any, ...] = (
        CheckConstraint(
            "status IN ('owned','wishlist','archived')", name="status_values"
        ),
        CheckConstraint(
            "wear_state IN ('clean','worn_once','dirty','laundry')",
            name="wear_state_values",
        ),
        CheckConstraint(
            "formality IS NULL OR formality BETWEEN 1 AND 5", name="formality_range"
        ),
        CheckConstraint(
            "warmth IS NULL OR warmth BETWEEN 1 AND 5", name="warmth_range"
        ),
        CheckConstraint(
            "material_confidence IS NULL OR material_confidence BETWEEN 0 AND 1",
            name="material_confidence_range",
        ),
        # 新版 M1.md：索引仅此一个，且是普通 B-Tree。
        Index("ix_garments_owner_category_status", "owner_id", "category", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="owned"
    )
    # category 为自由文本；合法值清单（48 类）在 attribute_dimensions.CATEGORY_VALUES，
    # 由 M2 的 vision prompt 使用，数据库层不冻结。
    category: Mapped[str] = mapped_column(Text, nullable=False)
    subcategory: Mapped[str | None] = mapped_column(Text, nullable=True)

    # colors: 数组，每项 {name, hex?, role}；role 示例 primary/secondary/accent。
    colors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    material_guess: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    formality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    style_tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    occasion_tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    warmth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 相对路径：/images/{garment_id}.jpg
    image_url: Mapped[str] = mapped_column(Text, nullable=False)

    # 图像 embedding，用于查重 / 相似度；全表扫描时不依赖向量索引。
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    vision_raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 扩展属性 JSONB：键名与取值必须来自 attribute_dimensions.ATTRIBUTE_DIMENSIONS。
    # 校验只放在 Pydantic 层（app/schemas.py），数据库不加 CHECK，
    # 这样以后加维度不必改迁移。
    attributes: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    # ---- 新版 M1.md 新增：穿着状态与最近穿着记录 ----
    wear_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="clean",
        server_default=text("'clean'"),
    )
    # 最近 30 天穿着日期，ISO 字符串数组。
    recent_wear_dates: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    last_worn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class OutfitSession(Base):
    """每次推荐请求一行，用于审计、反馈和偏好学习。"""

    __tablename__ = "outfit_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 以下 JSONB 的具体形状由 M3 契约冻结，M1 只提供容器，不猜键名。
    request_context: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    layering_plan: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    recommendations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missing_slots: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # U4 模块三：一次推荐里 Planner / Stylist 各次调用的 token 用量。
    # 形状：{"calls":[...], "prompt_tokens":int, "completion_tokens":int,
    #        "total_tokens":int}；旧行由 0002 迁移填 '{}'。
    token_usage: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # M1.md 格式：{model_name}|{image_format}|{n_images}
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Feedback(Base):
    """针对某次推荐中第 outfit_index 套搭配的反馈。"""

    __tablename__ = "feedback"

    __table_args__: tuple[Any, ...] = (
        CheckConstraint(
            "signal IN ('liked','disliked','worn','not_worn','regenerated')",
            name="signal_values",
        ),
        CheckConstraint(
            "wear_state_after IS NULL OR wear_state_after IN "
            "('clean','worn_once','dirty','laundry')",
            name="wear_state_after_values",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("outfit_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    outfit_index: Mapped[int] = mapped_column(Integer, nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    wear_state_after: Mapped[str | None] = mapped_column(String(32), nullable=True)
    free_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class UserPreference(Base):
    """偏好文本的当前快照，每个用户一行（不是单用户假设）。"""

    __tablename__ = "user_preferences"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    preference_text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    updated_at: Mapped[datetime] = _updated_at()


class UserPreferenceHistory(Base):
    """偏好文本的历史版本；仓库层只保留最近 5 份（新版 M1.md）。"""

    __tablename__ = "user_preferences_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    preference_text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class WishlistEvaluation(Base):
    """购前决策记录。"""

    __tablename__ = "wishlist_evaluations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_garment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("garments.id", ondelete="CASCADE"),
        nullable=False,
    )
    existing_similar_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    feasible_outfit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    scene_coverage: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    llm_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    llm_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
