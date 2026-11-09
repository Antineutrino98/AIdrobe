"""视觉提取（M2）：图片压缩 → VISION_EXTRACTOR 调用 → 结构化 JSON 清洗。

契约要点（新版 AIdrobe.md + M2.md）
----------------------------------
- **模型调用一律走 `app/llm_config.get_client(ModelRole.VISION_EXTRACTOR)`**；
  代码中不出现任何模型名、base_url、api_key（禁止硬编码）。
- 提示词模板在 `app/prompts/vision_extraction.md`，枚举由
  `app/attribute_dimensions.py` 运行时注入，不在这里硬编码。
- category 使用 48 类清单：缺失/非字符串 → 重试一次 → 仍失败抛
  `VisionExtractionError`（路由转 422）；只是不在清单 → 归 `other`、
  记日志、照常入库（国标 99 收容类目）。
- attributes 非法键/值 → 丢弃该键值对、记日志、不阻止入库。
- material_guess 为 null → 不阻止入库、不追问用户。
- description / category_confidence 在 M1 的 garments 表里**没有独立列**，
  由 ingest 层一并写入 `vision_raw`（JSONB）。
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.attribute_dimensions import (
    ATTRIBUTE_DIMENSIONS,
    CATEGORY_FALLBACK,
    CATEGORY_VALUES,
)
from app.llm_config import (
    model_extra_kwargs,
    ImageBlockError,
    ModelConfigError,
    ModelRole,
    build_image_block,
    get_client,
    get_image_format,
    get_model_name,
)
from app.prompts import build_vision_system_prompt

logger: logging.Logger = logging.getLogger(__name__)

MAX_EDGE: int = 1024
JPEG_QUALITY: int = 85
MAX_ATTEMPTS: int = 2  # 首次 + 重试一次（M2.md）
LOW_CONFIDENCE_THRESHOLD: float = 0.5


class VisionConfigurationError(RuntimeError):
    """模型角色环境变量没配好；重试没有意义，直接告知调用方。"""


class VisionExtractionError(RuntimeError):
    """两次都没解析出合法结果，路由应返回 422。"""


class ImageProcessingError(RuntimeError):
    """上传内容不是可识别的图片，或本地图片读取失败。"""


class ExtractedGarment(BaseModel):
    """视觉模型返回的结构化结果（清洗后）。"""

    category: str
    # 清洗前的原始响应，用于写入 garments.vision_raw 便于追溯。
    raw_response: dict[str, Any] = Field(default_factory=dict, exclude=True)
    subcategory: str | None = None
    colors: list[dict[str, Any]] = Field(default_factory=list)
    material_guess: str | None = None
    material_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    formality: int | None = Field(default=None, ge=1, le=5)
    seasons: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    occasion_tags: list[str] = Field(default_factory=list)
    warmth: int | None = Field(default=None, ge=1, le=5)
    pattern: str | None = None
    description: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    category_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    category_candidates: list[str] = Field(default_factory=list)

    def confirmation_options(self) -> list[str]:
        """低置信度时给用户的两个候选品类（来自 AI 的判断）。"""
        options: list[str] = [
            value for value in self.category_candidates if value in CATEGORY_VALUES
        ][:2]
        if self.category not in options:
            options.insert(0, self.category)
        if len(options) < 2 and CATEGORY_FALLBACK not in options:
            options.append(CATEGORY_FALLBACK)
        return options[:2]


def prepare_image(data: bytes) -> bytes:
    """Pillow 压缩：最长边 ≤ 1024px，统一转 JPEG quality 85（M2.md）。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            rgb = img.convert("RGB")
            width, height = rgb.size
            if max(width, height) > MAX_EDGE:
                scale: float = MAX_EDGE / max(width, height)
                rgb = rgb.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    Image.Resampling.LANCZOS,
                )
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProcessingError("无法读取图片，请上传有效的图片文件") from exc


def _parse_json(content: str) -> dict[str, Any]:
    text: str = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data: Any = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return data


def _clean_attributes(raw: Any) -> dict[str, str]:
    """非法键/值一律丢弃并记日志，绝不因 attributes 让整条记录失败。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if key not in ATTRIBUTE_DIMENSIONS:
            logger.warning("丢弃未知属性维度 key=%r", key)
            continue
        allowed: list[str] = ATTRIBUTE_DIMENSIONS[key]["values"]
        if not isinstance(value, str) or value not in allowed:
            logger.warning("丢弃非法属性值 %s=%r（允许 %s）", key, value, allowed)
            continue
        cleaned[key] = value
    return cleaned


def _clean_category(raw: Any) -> str | None:
    """None 表示「缺失/非字符串」→ 触发重试；不在清单则归 other。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    value: str = raw.strip()
    if value not in CATEGORY_VALUES:
        logger.warning(
            "category=%r 不在 48 类清单，归入收容类目 %r", value, CATEGORY_FALLBACK
        )
        return CATEGORY_FALLBACK
    return value


def _clean_candidates(raw: Any, primary: str) -> list[str]:
    result: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item in CATEGORY_VALUES and item not in result:
                result.append(item)
            if len(result) >= 2:
                break
    if primary not in result:
        result.insert(0, primary)
    return result[:2]


def _extract_text(choice: Any) -> str:
    """兼容 content 为 str 或内容块数组两种返回形式。"""
    content: Any = choice.message.content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return str(content or "")


async def _call_vision_api(
    image_path: Path, context_text: str | None
) -> dict[str, Any]:
    """通过统一模型客户端调用 VISION_EXTRACTOR（OpenAI 兼容）。"""
    try:
        client = get_client(ModelRole.VISION_EXTRACTOR)
        model: str = get_model_name(ModelRole.VISION_EXTRACTOR)
        image_format: str = get_image_format(ModelRole.VISION_EXTRACTOR)
    except ModelConfigError as exc:
        raise VisionConfigurationError(str(exc)) from exc

    if image_format != "data_url":
        # 本地图片没有公网 URL，只能走 base64；配置成 url 时强制回退。
        logger.warning(
            "VISION_IMAGE_FORMAT=%s 不适用于本地图片，本次改用 data_url",
            image_format,
        )
        image_format = "data_url"

    try:
        image_block = build_image_block(str(image_path), image_format)
    except ImageBlockError as exc:
        raise ImageProcessingError(str(exc)) from exc

    user_content: list[dict[str, Any]] = [image_block]
    if context_text:
        user_content.append(
            {"type": "text", "text": f"Product page text: {context_text}"}
        )

    completion = await client.chat.completions.create(
        model=model,
        **model_extra_kwargs(model),
        messages=[
            {"role": "system", "content": build_vision_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
    )
    return _parse_json(_extract_text(completion.choices[0]))


async def extract_garment_attributes(
    image_path: Path, context_text: str | None = None
) -> ExtractedGarment:
    """调用视觉模型并清洗；category 解析失败时重试一次，仍失败则抛错。"""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw: dict[str, Any] = await _call_vision_api(image_path, context_text)
            original: dict[str, Any] = dict(raw)  # 清洗前留档
            category: str | None = _clean_category(raw.get("category"))
            if category is None:
                raise ValueError("category 缺失或不是非空字符串")
            raw["category"] = category
            raw["attributes"] = _clean_attributes(raw.get("attributes", {}))
            raw["category_candidates"] = _clean_candidates(
                raw.get("category_candidates"), category
            )
            if raw.get("category_confidence") is None:
                # 模型没给置信度时视为「有把握」，避免无谓拦截入库。
                raw["category_confidence"] = 1.0
            result: ExtractedGarment = ExtractedGarment.model_validate(raw)
            result.raw_response = original
            return result
        except (VisionConfigurationError, ImageProcessingError):
            raise
        except Exception as exc:  # noqa: BLE001 - 任何解析失败都要触发重试
            last_error = exc
            logger.warning("视觉解析第 %d/%d 次失败：%s", attempt, MAX_ATTEMPTS, exc)
    raise VisionExtractionError("请拍摄更清晰的平铺图") from last_error
