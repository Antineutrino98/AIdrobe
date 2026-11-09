"""Alembic 环境配置（PGlite / PostgreSQL 17 WASM）。

- 离线模式（`alembic upgrade head --sql`）：不连库，只生成 SQL 文本，
  便于在没有数据库的机器上检查迁移链是否写得干净。
- 在线模式（`alembic upgrade head`）：
  1. 若显式设置了 DATABASE_URL（外部已启动 PGlite 服务），直接连它；
  2. 否则经 `db_gateway.open_database()` 打开唯一句柄 —— 这一步会
     自动加文件锁、做启动前备份、启动底层后端，避免 Alembic 与业务进程
     同时写同一个数据目录；
  3. 迁移结束后关闭句柄并释放锁。

连接串/数据目录一律来自环境变量，不硬编码。
"""
from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config, create_async_engine

from app.db import Base, DATABASE_URL_ENV, OFFLINE_PLACEHOLDER_URL

# 必须导入 models，否则 Base.metadata 是空的，autogenerate 会以为没有表。
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None and not config.attributes.get(
    "skip_logging_config"
):
    from logging.config import fileConfig

    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """离线模式只需要语法合法的 PG 连接串；真库由网关在在线模式提供。"""
    return os.environ.get(DATABASE_URL_ENV, OFFLINE_PLACEHOLDER_URL)


def run_migrations_offline() -> None:
    """只拼 SQL，不连数据库。"""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    """在线迁移；全程只通过 db_gateway 拿唯一句柄。"""
    from app.db_gateway import close_database, open_database

    explicit_url: str = os.environ.get(DATABASE_URL_ENV, "").strip()
    engine: AsyncEngine | None = None
    handle = None

    try:
        if explicit_url:
            engine = create_async_engine(
                explicit_url,
                poolclass=pool.NullPool,
                connect_args={"server_settings": {}, "ssl": False},
            )
        else:
            data_dir = os.environ.get("PGLITE_DATA_DIR", "data/pglite")
            backup_dir = os.environ.get("PGLITE_BACKUP_DIR", "data/pglite-backups")
            handle = await open_database(data_dir, backup_dir)
            engine = handle.engine

        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        if explicit_url and engine is not None:
            await engine.dispose()
        if handle is not None:
            await close_database(handle)


def run_migrations_online() -> None:
    """在线迁移。

    1. 若调用方（app/main.py）通过 `config.attributes["connection"]` 注入了
       已打开的同步连接，直接复用 —— PGlite 只允许一个活动连接，这是
       lifespan 内迁移的唯一安全方式；
    2. 否则维持原行为：独立运行时经 db_gateway 打开唯一句柄。
    """
    injected: Connection | None = config.attributes.get("connection")
    if injected is not None:
        do_run_migrations(injected)
        return
    asyncio.run(_run_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
