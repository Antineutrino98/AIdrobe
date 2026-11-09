"""用户管理：列表 / 新建 / 改名 / 删除。

没有认证模块（M6 明确不做），所以这些接口不鉴权，只在本地单机使用。

删除是**级联**的：M1 给所有业务表的外键都写了 `ondelete="CASCADE"`，
删掉用户会同时删掉他名下的衣物、推荐会话、反馈、偏好历史、购前评估。
前端在删除前必须二次确认并把这句话说给用户。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import Garment, User
from app.repositories.postgres import PostgresUserRepository
from app.schemas import UserNamePayload, UserSchema
from app.services.recommend_policy import POLICY_KEY, parse_policy

router: APIRouter = APIRouter(tags=["users"])


@router.get("/users", response_model=list[UserSchema])
async def list_users(
    session: AsyncSession = Depends(get_db),
) -> list[UserSchema]:
    """按创建时间从旧到新返回用户（最多 100 个）。"""
    return await PostgresUserRepository(session).list_users()


@router.post("/users", response_model=UserSchema, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserNamePayload,
    session: AsyncSession = Depends(get_db),
) -> UserSchema:
    """新建一个用户名。同名允许存在（家庭里可能有重名），靠 id 区分。"""
    user = User(display_name=body.display_name, settings={})
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserSchema.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserSchema)
async def rename_user(
    user_id: uuid.UUID,
    body: UserNamePayload,
    session: AsyncSession = Depends(get_db),
) -> UserSchema:
    """改名；找不到用户返回 404。"""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到这个用户")
    user.display_name = body.display_name
    await session.commit()
    await session.refresh(user)
    return UserSchema.model_validate(user)


@router.get("/users/{user_id}/policy")
async def get_policy(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """读取该用户的推荐 / 穿着策略；没设过就返回默认值。"""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到这个用户")
    return parse_policy(dict(user.settings or {})).as_dict()


@router.put("/users/{user_id}/policy")
async def put_policy(
    user_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """保存推荐 / 穿着策略。非法值会被归一化成默认值，不会写坏数据。"""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到这个用户")
    normalized = parse_policy({POLICY_KEY: body}).as_dict()
    settings = dict(user.settings or {})
    settings[POLICY_KEY] = normalized
    user.settings = settings
    # JSONB 是 mutable dict，必须显式标记，否则 SQLAlchemy 可能不写回。
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(user, "settings")
    await session.commit()
    return normalized


@router.get("/users/{user_id}/summary")
async def user_summary(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """删除确认弹窗用：告诉用户会连带删掉多少件衣物。"""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到这个用户")
    count = (
        await session.execute(
            select(func.count()).select_from(Garment).where(
                Garment.owner_id == user_id
            )
        )
    ).scalar_one()
    return {"id": str(user.id), "display_name": user.display_name, "garment_count": int(count)}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """删除用户；数据库外键 CASCADE 会连带删掉该用户的全部业务数据。

    注意：`data/images/` 下已落盘的图片文件不会被删除（留着不影响使用，
    也不会被新用户误引用，因为 id 是 UUID）。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到这个用户")
    count = (
        await session.execute(
            select(func.count()).select_from(Garment).where(
                Garment.owner_id == user_id
            )
        )
    ).scalar_one()
    name = user.display_name
    # 显式删一遍业务表，避免某些 PGlite 版本不执行 ON DELETE CASCADE。
    # 顺序按外键依赖从子到父。
    from app.models import (
        Feedback,
        OutfitSession,
        UserPreference,
        UserPreferenceHistory,
        WishlistEvaluation,
    )

    for model in (
        Feedback,
        WishlistEvaluation,
        OutfitSession,
        UserPreferenceHistory,
        UserPreference,
        Garment,
    ):
        await session.execute(
            delete(model).where(model.owner_id == user_id)  # type: ignore[attr-defined]
        )
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
    return {
        "deleted_user_id": str(user_id),
        "display_name": name,
        "deleted_garments": int(count),
    }
