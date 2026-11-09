"""入库编排（M2）：图片/链接 → 视觉提取 → 置信度处理 → 落库。

路由只调用本层；本层只调用 repository 与 services/vision、services/embedding，
不直接写 SQL、不直接发 LLM 请求（AIdrobe.md 编码规则）。

关于「低置信度不自动入库」
--------------------------
M2.md 要求 category_confidence < 0.5 时不入库，但响应契约又要返回 garment_id。
这里用进程内 `_pending` 暂存提取结果，返回的 garment_id 是**待确认记录的主键**；
用户在 confirm-category 里选定品类后，才用**同一个 id**真正写入 garments 表，
因此用户前后拿到的 id 一致，且数据库里不会留下品类存疑的脏数据。

局限：进程重启后待确认记录会丢失。M2 是单模块本地开发，先用进程内暂存；
将来需要跨实例/持久化时应换成数据库表或缓存（属后续模块范围）。

字段落库方式（新版 M1 没有独立列）
----------------------------------
`description` / `category_confidence` / `category_candidates` 没有对应数据库列，
统一写入 `garments.vision_raw`（JSONB），同时在响应 `extracted` 里返回。
这样既满足 M2 的响应契约，也不违反「Alembic 仅一条初始迁移」。
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.attribute_dimensions import CATEGORY_FALLBACK, CATEGORY_VALUES
from app.repositories.base import GarmentRepository
from app.schemas import (
    GarmentCreate,
    GarmentCreateResult,
    GarmentSchema,
    GarmentStatus,
)
from app.services.embedding import generate_embedding
from app.services.vision import (
    LOW_CONFIDENCE_THRESHOLD,
    ExtractedGarment,
    extract_garment_attributes,
    prepare_image,
)

logger: logging.Logger = logging.getLogger(__name__)

# AIdrobe.md 目录约定：data/images/；image_url 存 /images/{garment_id}.jpg
IMAGE_STORAGE_DIR_ENV: str = "IMAGE_STORAGE_DIR"
DEFAULT_IMAGE_STORAGE_DIR: str = "data/images"


class LinkParsingError(RuntimeError):
    """商品页找不到主图，或链接无法访问。"""


class PendingNotFoundError(RuntimeError):
    """待确认记录不存在或已过期。"""


@dataclass
class PendingIngest:
    garment_id: uuid.UUID
    owner_id: uuid.UUID
    extracted: ExtractedGarment
    image_path: Path
    embedding: list[float] | None


_pending: dict[uuid.UUID, PendingIngest] = {}


def _clean_colors(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐 colors 的必填项，最多 3 个（M2.md）。"""
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name: Any = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        role: Any = item.get("role")
        if role not in {"primary", "secondary", "accent"}:
            role = "primary"
        entry: dict[str, Any] = {"name": name.strip(), "role": role}
        if isinstance(item.get("hex"), str):
            entry["hex"] = item["hex"]
        result.append(entry)
        if len(result) >= 3:
            break
    return result


def image_dir() -> Path:
    """本地图片目录；相对路径按当前工作目录解析。

    兼容 M7.md 的 `IMAGE_DIR` 与 M2 既有的 `IMAGE_STORAGE_DIR`：
    优先 `IMAGE_STORAGE_DIR`，其次 `IMAGE_DIR`，最后默认 `data/images`。
    """
    configured: str = (
        os.environ.get(IMAGE_STORAGE_DIR_ENV, "").strip()
        or os.environ.get("IMAGE_DIR", "").strip()
        or DEFAULT_IMAGE_STORAGE_DIR
    )
    path = Path(configured)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


async def fetch_from_url(url: str) -> tuple[bytes, str | None]:
    """纯图片链接直接下载；网页则提取 og:image 与标题/描述文本（M2.md）。"""
    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True
        ) as client:
            response = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (AIdrobe M2)"}
            )
            response.raise_for_status()
            content_type: str = response.headers.get("content-type", "")
            if content_type.startswith("image/"):
                return response.content, None

            soup = BeautifulSoup(response.text, "html.parser")
            og_image = soup.find("meta", attrs={"property": "og:image"})
            image_url: str | None = (
                str(og_image.get("content"))
                if og_image and og_image.get("content")
                else None
            )
            if not image_url:
                first_img = soup.find("img")
                image_url = (
                    str(first_img.get("src"))
                    if first_img and first_img.get("src")
                    else None
                )
            if not image_url:
                raise LinkParsingError("商品页里找不到主图（og:image / img）")

            image_response = await client.get(urljoin(url, image_url))
            image_response.raise_for_status()

            title_tag = soup.find("meta", attrs={"property": "og:title"}) or soup.find(
                "title"
            )
            desc_tag = soup.find("meta", attrs={"property": "og:description"})
            title: str = (
                title_tag.get_text(strip=True)
                if title_tag and hasattr(title_tag, "get_text")
                else (str(title_tag.get("content", "")) if title_tag else "")
            )
            description: str = (
                str(desc_tag.get("content", "")) if desc_tag else ""
            )
            context: str = ". ".join(part for part in (title, description) if part)
            return image_response.content, context or None
    except LinkParsingError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成可读的 422
        raise LinkParsingError(f"无法读取链接内容：{exc}") from exc


class IngestService:
    def __init__(self, repository: GarmentRepository) -> None:
        self._repo: GarmentRepository = repository

    async def ingest_image(
        self,
        owner_id: uuid.UUID,
        data: bytes,
        context_text: str | None = None,
        *,
        status: GarmentStatus = GarmentStatus.OWNED,
        skip_confirmation: bool = False,
    ) -> GarmentCreateResult:
        """`status=WISHLIST` 用于购前候选：不进衣橱列表，也不进推荐候选池。

        用户反馈：购前上传的图曾以 owned 入库，评估还没跑完就出现在推荐
        结果里。M1 冻结的 wishlist 状态正好用来隔离这类临时候选。
        """
        image_jpeg: bytes = prepare_image(data)
        garment_id: uuid.UUID = uuid.uuid4()
        # M2.md：图片压缩后写入 data/images/{garment_id}.jpg
        image_path: Path = image_dir() / f"{garment_id}.jpg"
        image_path.write_bytes(image_jpeg)

        extracted: ExtractedGarment = await extract_garment_attributes(
            image_path, context_text
        )
        vector: list[float] | None = await generate_embedding(image_jpeg)

        if (
            extracted.category_confidence < LOW_CONFIDENCE_THRESHOLD
            and not skip_confirmation
        ):
            _pending[garment_id] = PendingIngest(
                garment_id=garment_id,
                owner_id=owner_id,
                extracted=extracted,
                image_path=image_path,
                embedding=vector,
            )
            return GarmentCreateResult(
                garment_id=garment_id,
                status="needs_category_confirmation",
                extracted=extracted.model_dump(),
                confirmation_options=extracted.confirmation_options(),
            )

        garment: GarmentSchema = await self._persist(
            garment_id, owner_id, extracted, image_path, vector, status
        )
        return GarmentCreateResult(
            garment_id=garment.id,
            status="auto_accepted",
            extracted=extracted.model_dump(),
        )

    async def ingest_url(
        self,
        owner_id: uuid.UUID,
        url: str,
        *,
        status: GarmentStatus = GarmentStatus.OWNED,
        skip_confirmation: bool = False,
    ) -> GarmentCreateResult:
        data, context_text = await fetch_from_url(url)
        return await self.ingest_image(
            owner_id,
            data,
            context_text,
            status=status,
            skip_confirmation=skip_confirmation,
        )

    async def confirm_category(
        self, garment_id: uuid.UUID, category: str
    ) -> GarmentSchema:
        pending: PendingIngest | None = _pending.pop(garment_id, None)
        if pending is None:
            raise PendingNotFoundError(f"待确认记录不存在或已过期：{garment_id}")
        value: str = category.strip() if isinstance(category, str) else ""
        if value not in CATEGORY_VALUES:
            logger.warning(
                "confirm 的 category=%r 不在 48 类清单，归入 %r",
                value,
                CATEGORY_FALLBACK,
            )
            value = CATEGORY_FALLBACK
        pending.extracted.category = value
        return await self._persist(
            pending.garment_id,
            pending.owner_id,
            pending.extracted,
            pending.image_path,
            pending.embedding,
        )

    async def _persist(
        self,
        garment_id: uuid.UUID,
        owner_id: uuid.UUID,
        extracted: ExtractedGarment,
        image_path: Path,
        vector: list[float] | None,
        status: GarmentStatus = GarmentStatus.OWNED,
    ) -> GarmentSchema:
        # 原始响应 + 三个没有独立列的字段，统一进 vision_raw。
        vision_raw: dict[str, Any] = dict(extracted.raw_response)
        vision_raw["description"] = extracted.description
        vision_raw["category_confidence"] = extracted.category_confidence
        vision_raw["category_candidates"] = extracted.category_candidates

        payload = GarmentCreate(
            id=garment_id,
            owner_id=owner_id,
            status=status,
            category=extracted.category,
            subcategory=extracted.subcategory,
            colors=_clean_colors(extracted.colors),
            material_guess=extracted.material_guess,
            material_confidence=extracted.material_confidence,
            formality=extracted.formality,
            seasons=extracted.seasons,
            style_tags=extracted.style_tags,
            occasion_tags=extracted.occasion_tags,
            warmth=extracted.warmth,
            pattern=extracted.pattern,
            image_url=f"/images/{garment_id}.jpg",
            embedding=vector,
            vision_raw=vision_raw,
            attributes=extracted.attributes,
        )
        logger.info("入库：%s（图片 %s）", garment_id, image_path)
        return await self._repo.create(payload)
