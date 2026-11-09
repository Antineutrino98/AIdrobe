"""FastAPI 依赖：数据库会话与当前用户（M7）。

设计说明
--------
- 数据库只认 `app.state.db`（lifespan 里由 db_gateway 打开的 DatabaseHandle），
  本文件不自行打开 PGlite。
- 现有 M2–M5 路由仍走 `ensure_database()` + `get_session_factory()`；
  那两个函数在 lifespan 之后会复用同一个句柄，行为不变。
- M1 没有认证模块，`get_current_user` 只能从 query / header / 环境变量取
  owner_id；**这不是认证**，只是给后续模块一个统一入口。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

from fastapi import Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_OWNER_ID_ENV: str = "AIDROBE_DEFAULT_OWNER_ID"


def get_session_factory_from_app(
    request: Request,
) -> async_sessionmaker[AsyncSession]:
    """从 `app.state.db` 取唯一引擎，构造请求级会话工厂。"""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise RuntimeError(
            "数据库尚未初始化：请确认应用经过 lifespan 启动（app.state.db 为空）。"
        )
    return async_sessionmaker(
        bind=db.engine,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """每个请求一个 AsyncSession；退出时自动关闭。"""
    factory = get_session_factory_from_app(request)
    async with factory() as session:
        yield session


async def get_current_user(
    owner_id: uuid.UUID | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> uuid.UUID:
    """取当前用户 id（**不是认证**）。

    优先级：query.owner_id → query.user_id → header X-User-Id →
    环境变量 AIDROBE_DEFAULT_OWNER_ID。都没有则 422。
    """
    candidate: uuid.UUID | None = owner_id or user_id
    if candidate is None and x_user_id:
        try:
            candidate = uuid.UUID(x_user_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"X-User-Id 不是合法 UUID：{x_user_id!r}",
            ) from exc
    if candidate is not None:
        return candidate

    configured: str = os.environ.get(DEFAULT_OWNER_ID_ENV, "").strip()
    if configured:
        try:
            return uuid.UUID(configured)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"环境变量 {DEFAULT_OWNER_ID_ENV} 不是合法 UUID："
                    f"{configured!r}"
                ),
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            "缺少用户标识。M1 没有认证模块，请在请求里提供 owner_id / user_id，"
            f"或设置环境变量 {DEFAULT_OWNER_ID_ENV}。"
        ),
    )
