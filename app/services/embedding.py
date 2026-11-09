"""图像 embedding 生成（M2）。

M2.md 要求：入库成功后用 FashionCLIP / Marqo-FashionCLIP（API 或独立推理服务）
生成 512 维 embedding 写入 garments.embedding；服务不可用时允许 null 并记录日志，
**绝不因为 embedding 失败回滚入库**。

M2.md 没有指定具体服务地址，所以这里做成可插拔接口：
- `EMBEDDING_API_URL` 未设置 → 直接返回 None（本地开发 / 无服务时可用）。
- 设置后向其 POST OpenAI 风格的 JSON，约定响应为 `{"embedding": [512 个数字]}`；
  若你的服务返回结构不同，只需改本文件 `_parse_embedding`，不影响其它模块。
- 任何异常（超时、5xx、维度不符）都降级为 None，不向上抛。
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from app.models import EMBEDDING_DIM

logger: logging.Logger = logging.getLogger(__name__)

EMBEDDING_API_URL_ENV: str = "EMBEDDING_API_URL"
EMBEDDING_API_KEY_ENV: str = "EMBEDDING_API_KEY"
EMBEDDING_MODEL_ENV: str = "EMBEDDING_MODEL"
# 不设默认模型名：AIdrobe.md 禁止在代码中硬编码模型名。
# 未配置 EMBEDDING_MODEL 时直接跳过（返回 None），不影响入库。


def _parse_embedding(body: dict[str, Any]) -> list[float] | None:
    raw: Any = body.get("embedding")
    if not isinstance(raw, list) or len(raw) != EMBEDDING_DIM:
        logger.warning(
            "embedding 维度不符：期望 %d，实际 %s",
            EMBEDDING_DIM,
            len(raw) if isinstance(raw, list) else type(raw).__name__,
        )
        return None
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        logger.warning("embedding 含非数字值")
        return None


async def generate_embedding(image_jpeg: bytes) -> list[float] | None:
    """生成 512 维向量；服务不可用或返回异常时一律返回 None。

    兼容 M7.md 的 `EMBEDDING_API_BASE` 与既有 `EMBEDDING_API_URL`：
    优先 `EMBEDDING_API_URL`，回退 `EMBEDDING_API_BASE`。
    """
    url: str = (
        os.environ.get(EMBEDDING_API_URL_ENV, "").strip()
        or os.environ.get("EMBEDDING_API_BASE", "").strip()
    )
    model: str = os.environ.get(EMBEDDING_MODEL_ENV, "").strip()
    if not url or not model:
        logger.info(
            "%s（或 EMBEDDING_API_BASE）与 %s 未配置，跳过 embedding 生成"
            "（入库不受影响）",
            EMBEDDING_API_URL_ENV,
            EMBEDDING_MODEL_ENV,
        )
        return None

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key: str = os.environ.get(EMBEDDING_API_KEY_ENV, "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "input": base64.b64encode(image_jpeg).decode("ascii"),
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        return _parse_embedding(body)
    except Exception as exc:  # noqa: BLE001 - 必须降级，不能影响入库
        logger.warning("embedding 服务不可用，已降级为 null：%s", exc)
        return None
