"""ORM 元数据与「唯一句柄」兼容层（M1）。

职责边界（AIdrobe.md「数据库规则」）
------------------------------------
- **打开/关闭数据库的唯一入口是 `app/db_gateway.py`**。本模块不再自行创建
  引擎、不再读取 DATABASE_URL 去连库，也不缓存任何全局 AsyncEngine。
- 本文件只放两样东西：
  1. `Base` / `NAMING_CONVENTION`：SQLAlchemy ORM 元数据，供 models.py 与
     Alembic 使用（纯声明，不涉及任何连接）。
  2. `get_session_factory()`：给 M2 已写好的代码用的兼容层。它从
     `db_gateway.get_current_handle()` 取唯一引擎，绝不绕过网关。

为什么保留兼容层：M2 的 app/routers/ingest.py 已经 import 了
`get_session_factory()`。本轮按用户要求「M2 先不动」，保留这个同名函数
可以让 M2 文件至少能被正常导入，不至于让 pytest 在收集阶段就崩掉。
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL_ENV: str = "DATABASE_URL"

# 离线模式（alembic upgrade head --sql）不连库，只需要一个语法合法的 PG 连接串。
# 真库连接串一律由 db_gateway 在运行时提供，不在这里硬编码。
OFFLINE_PLACEHOLDER_URL: str = (
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/postgres"
)

# 稳定的约束命名约定：Alembic 对比迁移时不会因为随机名字产生漂移。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的公共基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_database_url() -> str:
    """供 Alembic 离线生成 SQL 使用；在线模式的真实连接串由网关写入此变量。"""
    return os.environ.get(DATABASE_URL_ENV, OFFLINE_PLACEHOLDER_URL)


_ENSURE_LOCK: asyncio.Lock = asyncio.Lock()
DEFAULT_DATA_DIR: str = "data/pglite"
DEFAULT_BACKUP_DIR: str = "data/pglite-backups"


async def ensure_database(
    data_dir: str | None = None, backup_dir: str | None = None
) -> object:
    """确保进程内有唯一句柄；M2/M3 的 router 共用这个入口。

    仍然只通过 db_gateway 打开数据库；这里只做「幂等 + 串行化」。
    """
    from app.db_gateway import DatabaseHandle, get_current_handle, open_database

    handle: DatabaseHandle | None = get_current_handle()
    if handle is not None:
        return handle
    async with _ENSURE_LOCK:
        handle = get_current_handle()
        if handle is None:
            handle = await open_database(
                data_dir or os.environ.get("PGLITE_DATA_DIR", DEFAULT_DATA_DIR),
                backup_dir
                or os.environ.get("PGLITE_BACKUP_DIR", DEFAULT_BACKUP_DIR),
            )
    return handle


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """兼容层：返回绑定到「网关唯一句柄」的会话工厂。

    数据库尚未打开时抛出可读错误，而不是返回一堆难以定位的 None。
    """
    # 延迟导入避免 db_gateway <-> db 的循环依赖。
    from app.db_gateway import get_current_handle

    handle = get_current_handle()
    if handle is None:
        raise RuntimeError(
            "数据库尚未打开。请先 await app.db_gateway.open_database("
            "data_dir, backup_dir) 取得 DatabaseHandle。"
        )
    engine: AsyncEngine = handle.engine
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
