"""衣物接口：列表 / 单件详情 / 状态标记 / 删除。

列表给「我的衣橱」用；`scope=shared`（界面显示为「全部衣橱」）时列出所有
用户的衣物，`scope=mine` 时只看当前用户。详情弹窗直接复用单件接口。
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import Garment
from app.repositories.postgres import PostgresGarmentRepository
from app.schemas import GarmentSchema

router: APIRouter = APIRouter(tags=["garments"])


class WearStatePayload(BaseModel):
    """U2：衣橱卡片上的「标记为待洗 / 洗干净了」。"""

    wear_state: Literal["clean", "worn_once", "dirty", "laundry"]


@router.patch("/garments/{garment_id}/wear-state")
async def set_wear_state(
    garment_id: uuid.UUID,
    body: WearStatePayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """只改穿着状态，不动品类、属性或任何展示文本。

    dirty / laundry 的单品会被推荐候选池自动排除（常识，由程序保证）。
    """
    obj: Garment | None = await session.get(Garment, garment_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到这件衣物",
        )
    obj.wear_state = body.wear_state
    await session.commit()
    return {"ok": True, "garment_id": str(garment_id), "wear_state": body.wear_state}


@router.get("/garments", response_model=list[GarmentSchema])
async def list_garments(
    owner_id: uuid.UUID | None = None,
    category: str | None = None,
    scope: str = "mine",
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> list[GarmentSchema]:
    """列出已入库的衣物，按创建时间从新到旧。

    `scope=shared`（全部衣橱）时忽略 owner_id，返回所有人的衣物；
    `scope=mine` 时只看当前用户。只返回 `status=owned` 的真实衣物。
    """
    repo = PostgresGarmentRepository(session)
    if scope in ("shared", "all") or owner_id is None:
        rows = await repo.list_all(limit=limit)
    else:
        rows = await repo.list_by_owner(owner_id, category=category, limit=limit)
    return [row for row in rows if row.status == "owned"]


@router.get("/garments/{garment_id}", response_model=GarmentSchema)
async def get_garment(
    garment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> GarmentSchema:
    """按 id 读取一件衣物；不存在返回 404。"""
    garment = await PostgresGarmentRepository(session).get(garment_id)
    if garment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到这件衣物",
        )
    return garment


@router.delete("/garments/{garment_id}")
async def delete_garment(
    garment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """丢掉/淘汰一件衣服：从衣橱里移除。

    数据库里 `feedback`、`wishlist_evaluations` 等对该单品的外键都是
    `ondelete=CASCADE`，删除会自动连带清掉相关记录。
    `data/images/` 下已落盘的图片文件保留不删（UUID 不会被复用，不影响使用）。
    """
    obj: Garment | None = await session.get(Garment, garment_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到这件衣物",
        )
    category = obj.category
    await session.execute(
        sa.delete(Garment).where(Garment.id == garment_id)
    )
    await session.commit()
    return {
        "deleted_garment_id": str(garment_id),
        "category": category,
    }
