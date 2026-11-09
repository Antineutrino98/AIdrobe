"""U4 模块三：outfit_sessions 增加 token_usage (JSONB)

Revision ID: 0002_token_usage
Revises: 0001_initial_schema

说明
----
按 U4 架构裁决 7 新增，**不改动 0001**。
列可空性为 NOT NULL + server_default '{}'，所以历史行会自动补空对象，
旧代码读取时不会炸。不建任何索引（AIdrobe.md 的索引约束保持不变）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_token_usage"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outfit_sessions",
        sa.Column(
            "token_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("outfit_sessions", "token_usage")
