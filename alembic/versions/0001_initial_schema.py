"""initial schema: 7 tables + pgvector extension + one B-Tree index

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-05（2026-11-06 按新版 M1.md 重写）

说明
----
这是 AIdrobe 的**唯一初始迁移**（新版 AIdrobe.md 明确「Alembic 仅初始迁移」）。
用户 2026-11-05 授权重写本文件并删除 0002，把 M2 曾引入的两列
合并回初始 schema；本文件与 app/models.py 一一对应。

与旧版 0001 的差异：
- 数据库目标改为 PGlite（PostgreSQL 17 WASM）；pgvector 扩展仍需要先创建。
- 新增 user_preferences_history 表；garments 增加 wear_state /
  recent_wear_dates / last_worn_at；outfit_sessions 增加 layering_plan /
  missing_slots 并移除 filtered_candidate_ids；feedback 增加 wear_state_after。
- 移除 description / category_confidence 两列及对应 CHECK。
- **只保留一个普通 B-Tree 索引** garments(owner_id, category, status)；
  删除 GIN、ivfflat、created_at DESC 及其他单列索引（AIdrobe.md 禁止
  GIN / 向量索引，几百件衣物全表扫描即可）。
- 所有业务表外键统一叫 owner_id。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_EMPTY_OBJECT = sa.text("'{}'::jsonb")
JSONB_EMPTY_ARRAY = sa.text("'[]'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _jsonb(name: str, *, is_array: bool = False) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(),
        server_default=JSONB_EMPTY_ARRAY if is_array else JSONB_EMPTY_OBJECT,
        nullable=False,
    )


def upgrade() -> None:
    # pgvector 扩展必须先于 embedding 列创建（PGlite 内置该扩展，实测通过）。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        _created_at(),
        _jsonb("settings"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    op.create_table(
        "garments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("subcategory", sa.Text(), nullable=True),
        _jsonb("colors", is_array=True),
        sa.Column("material_guess", sa.Text(), nullable=True),
        sa.Column("material_confidence", sa.Float(), nullable=True),
        sa.Column("formality", sa.Integer(), nullable=True),
        _jsonb("seasons", is_array=True),
        _jsonb("style_tags", is_array=True),
        _jsonb("occasion_tags", is_array=True),
        sa.Column("warmth", sa.Integer(), nullable=True),
        sa.Column("pattern", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=True),
        _jsonb("vision_raw"),
        sa.Column("user_note", sa.Text(), nullable=True),
        _jsonb("attributes"),
        # 新版 M1.md：穿着状态与最近穿着记录。
        sa.Column(
            "wear_state",
            sa.String(length=32),
            server_default=sa.text("'clean'"),
            nullable=False,
        ),
        _jsonb("recent_wear_dates", is_array=True),
        sa.Column("last_worn_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_garments_owner_id_users", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_garments"),
        # 约束名传短名：Alembic 会套 naming convention 变成
        # ck_garments_<name>，与 app/models.py 完全一致。
        sa.CheckConstraint(
            "status IN ('owned','wishlist','archived')", name="status_values"
        ),
        sa.CheckConstraint(
            "wear_state IN ('clean','worn_once','dirty','laundry')",
            name="wear_state_values",
        ),
        sa.CheckConstraint(
            "formality IS NULL OR formality BETWEEN 1 AND 5", name="formality_range"
        ),
        sa.CheckConstraint(
            "warmth IS NULL OR warmth BETWEEN 1 AND 5", name="warmth_range"
        ),
        sa.CheckConstraint(
            "material_confidence IS NULL OR material_confidence BETWEEN 0 AND 1",
            name="material_confidence_range",
        ),
    )
    # 唯一索引：普通 B-Tree，不含 GIN / HNSW / ivfflat。
    op.create_index(
        "ix_garments_owner_category_status",
        "garments",
        ["owner_id", "category", "status"],
        unique=False,
    )

    op.create_table(
        "outfit_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        _jsonb("request_context"),
        _jsonb("layering_plan"),
        _jsonb("recommendations", is_array=True),
        _jsonb("missing_slots", is_array=True),
        # 格式：{model_name}|{image_format}|{n_images}
        sa.Column("model_used", sa.Text(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_outfit_sessions_owner_id_users", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outfit_sessions"),
    )

    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("outfit_index", sa.Integer(), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("wear_state_after", sa.String(length=32), nullable=True),
        sa.Column("free_text", sa.Text(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["session_id"], ["outfit_sessions.id"],
            name="fk_feedback_session_id_outfit_sessions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_feedback_owner_id_users", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
        sa.CheckConstraint(
            "signal IN ('liked','disliked','worn','not_worn','regenerated')",
            name="signal_values",
        ),
        sa.CheckConstraint(
            "wear_state_after IS NULL OR wear_state_after IN "
            "('clean','worn_once','dirty','laundry')",
            name="wear_state_after_values",
        ),
    )

    op.create_table(
        "user_preferences",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("preference_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_user_preferences_owner_id_users", ondelete="CASCADE",
        ),
        # 每个用户一行，不是单用户假设。
        sa.PrimaryKeyConstraint("owner_id", name="pk_user_preferences"),
    )

    op.create_table(
        "user_preferences_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("preference_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_user_preferences_history_owner_id_users", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_preferences_history"),
    )

    op.create_table(
        "wishlist_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_garment_id", sa.Uuid(), nullable=False),
        _jsonb("existing_similar_ids", is_array=True),
        sa.Column(
            "feasible_outfit_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        _jsonb("scene_coverage", is_array=True),
        sa.Column("llm_verdict", sa.Text(), nullable=False),
        sa.Column("llm_reasoning", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_wishlist_evaluations_owner_id_users", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_garment_id"], ["garments.id"],
            name="fk_wishlist_evaluations_candidate_garment_id_garments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wishlist_evaluations"),
    )


def downgrade() -> None:
    op.drop_index("ix_garments_owner_category_status", table_name="garments")

    # 按外键依赖顺序删除（子表先删）。
    op.drop_table("wishlist_evaluations")
    op.drop_table("user_preferences_history")
    op.drop_table("user_preferences")
    op.drop_table("feedback")
    op.drop_table("outfit_sessions")
    op.drop_table("garments")
    op.drop_table("users")

    op.execute("DROP EXTENSION IF EXISTS vector")
