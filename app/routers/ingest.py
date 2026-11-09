"""M2 入库接口：3 个 POST + 1 个本地图片读取。

AIdrobe.md：路由函数只做参数校验和调用 service，不允许直接写 SQL 或 LLM 调用。
全局 app 组装不在 M2 范围（由后续模块把这两个 router 挂上去）。

依赖注入
--------
`get_ingest_service` 需要数据库已打开。若进程内还没有句柄，它会经
`db_gateway.open_database()` 打开唯一句柄（幂等、加锁），这样 M2 不依赖
尚未存在的 `app.main` 也能独立运行；测试可用 dependency_overrides 覆盖。
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.db import ensure_database, get_session_factory
from app.repositories.postgres import PostgresGarmentRepository
from app.schemas import (
    GarmentStatus,
    ConfirmCategoryRequest,
    GarmentCreateResult,
    GarmentSchema,
    IngestUrlRequest,
)
from app.services.ingest import (
    IngestService,
    LinkParsingError,
    PendingNotFoundError,
    image_dir,
)
from app.services.vision import (
    ImageProcessingError,
    VisionConfigurationError,
    VisionExtractionError,
)

DEFAULT_OWNER_ID_ENV: str = "AIDROBE_DEFAULT_OWNER_ID"
IMAGE_NAME_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}\.jpg$")

router: APIRouter = APIRouter(prefix="/ingest", tags=["ingest"])
images_router: APIRouter = APIRouter(tags=["images"])


async def _ensure_database() -> None:
    """确保进程内有唯一句柄（与 M3 共用 app.db.ensure_database）。"""
    await ensure_database()


async def get_ingest_service() -> AsyncIterator[IngestService]:
    """每个请求一个 AsyncSession；不在路由里做全局 app 组装。"""
    await _ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        yield IngestService(PostgresGarmentRepository(session))


def _resolve_owner_id(raw: uuid.UUID | None) -> uuid.UUID:
    """owner_id 可选：请求里给了就用；否则读环境变量；都没有就 422。"""
    if raw is not None:
        return raw
    configured: str = os.environ.get(DEFAULT_OWNER_ID_ENV, "").strip()
    if not configured:
        raise HTTPException(
            status_code=422,
            detail=(
                "缺少 owner_id。M1 没有认证模块，请在请求里提供 owner_id，"
                f"或设置环境变量 {DEFAULT_OWNER_ID_ENV}。"
            ),
        )
    try:
        return uuid.UUID(configured)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"环境变量 {DEFAULT_OWNER_ID_ENV} 不是合法 UUID：{configured!r}",
        ) from exc


def _ingest_status(purpose: str | None) -> tuple[GarmentStatus, bool]:
    """购前候选 → wishlist + 跳过品类确认；普通入库 → owned。"""
    if purpose == "purchase":
        return GarmentStatus.WISHLIST, True
    return GarmentStatus.OWNED, False


@router.post(
    "/image", status_code=status.HTTP_201_CREATED, response_model=GarmentCreateResult
)
async def ingest_image(
    file: UploadFile = File(...),
    owner_id: uuid.UUID | None = Form(None),
    purpose: str | None = Form(None),
    service: IngestService = Depends(get_ingest_service),
) -> GarmentCreateResult:
    data: bytes = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="上传的图片是空文件")
    try:
        garment_status, skip = _ingest_status(purpose)
        return await service.ingest_image(
            _resolve_owner_id(owner_id),
            data,
            status=garment_status,
            skip_confirmation=skip,
        )
    except (VisionExtractionError, ImageProcessingError) as exc:
        raise HTTPException(
            status_code=422, detail="请拍摄更清晰的平铺图"
        ) from exc
    except VisionConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post(
    "/url", status_code=status.HTTP_201_CREATED, response_model=GarmentCreateResult
)
async def ingest_url(
    body: IngestUrlRequest,
    service: IngestService = Depends(get_ingest_service),
) -> GarmentCreateResult:
    try:
        garment_status, skip = _ingest_status(body.purpose)
        return await service.ingest_url(
            _resolve_owner_id(body.owner_id),
            body.url,
            status=garment_status,
            skip_confirmation=skip,
        )
    except LinkParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (VisionExtractionError, ImageProcessingError) as exc:
        raise HTTPException(
            status_code=422, detail="请换一张更清晰的商品主图"
        ) from exc
    except VisionConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/confirm-category", response_model=GarmentSchema)
async def confirm_category(
    body: ConfirmCategoryRequest,
    service: IngestService = Depends(get_ingest_service),
) -> GarmentSchema:
    try:
        return await service.confirm_category(body.garment_id, body.category)
    except PendingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="待确认记录不存在或已过期，请重新上传图片",
        ) from exc
    except VisionConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@images_router.get("/images/{filename}")
async def get_image(filename: str) -> FileResponse:
    """验收 6：把 data/images/{id}.jpg 通过 /images/{id}.jpg 暴露出去。"""
    if not IMAGE_NAME_PATTERN.match(filename):
        raise HTTPException(status_code=404, detail="图片不存在")
    path = image_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, media_type="image/jpeg")
