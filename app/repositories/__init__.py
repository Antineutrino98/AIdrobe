"""仓库层：抽象接口 + PostgreSQL 异步实现。"""
from __future__ import annotations

from app.repositories.base import (
    GarmentRepository,
    PreferenceEngineRepository,
    PreferenceRepository,
    PurchaseRepository,
    SessionRepository,
    UserRepository,
)
from app.repositories.postgres import (
    PostgresGarmentRepository,
    PostgresPreferenceEngineRepository,
    PostgresPreferenceRepository,
    PostgresPurchaseRepository,
    PostgresSessionRepository,
    PostgresUserRepository,
)

__all__: list[str] = [
    "GarmentRepository",
    "SessionRepository",
    "PreferenceRepository",
    "PreferenceEngineRepository",
    "PurchaseRepository",
    "UserRepository",
    "PostgresGarmentRepository",
    "PostgresSessionRepository",
    "PostgresPreferenceRepository",
    "PostgresPreferenceEngineRepository",
    "PostgresPurchaseRepository",
    "PostgresUserRepository",
]
