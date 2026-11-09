"""自检接口：后端连通性 + 三个模型角色能否真的调通。

U4 裁决 2：心跳杀进程机制已废弃，退出唯一主责归桌面窗口的 closed 事件。

为什么单独做一次真实调用：用户经常遇到「推荐失败但看不出为什么」。
这里用最小请求分别打三个角色，把「能不能连、多久、报什么错」直接返回。

视觉角色会**真的传一张图**，所以它同时回答了「这个模型到底能不能看图」。
"""
from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db

from app.llm_config import (
    JSON_MODE_HINT,
    ModelConfigError,
    ModelRole,
    get_client,
    get_model_name,
    model_extra_kwargs,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["system"])

_ROLE_MAP: dict[str, ModelRole] = {
    "vision": ModelRole.VISION_EXTRACTOR,
    "planner": ModelRole.TEXT_PLANNER,
    "stylist": ModelRole.MULTIMODAL_STYLIST,
}

TEST_TIMEOUT_SECONDS: float = 45.0


@router.get("/health")
async def health() -> dict[str, Any]:
    """只证明后端进程活着，不碰数据库、不碰模型。"""
    return {"ok": True}


def _tiny_image_data_url() -> str:
    """64×64 纯色小图，用来测「这个模型能不能吃图片输入」。"""
    from PIL import Image

    image = Image.new("RGB", (64, 64), (200, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _messages_for(role: ModelRole) -> list[dict[str, Any]]:
    if role == ModelRole.TEXT_PLANNER:
        return [
            {"role": "system", "content": "You are a health check." + JSON_MODE_HINT},
            {"role": "user", "content": 'Reply with {"ok": true}'},
        ]
    return [
        {
            "role": "system",
            "content": (
                "You are a vision health check. Look at the image and reply with "
                "one short JSON object." + JSON_MODE_HINT
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": _tiny_image_data_url()},
                },
                {"type": "text", "text": 'What is the main color? Reply {"color":"..."}'},
            ],
        },
    ]


@router.post("/test-model")
async def test_model(payload: dict[str, Any]) -> dict[str, Any]:
    """测试单个模型角色。`role` 取 vision / planner / stylist。

    视觉类角色会带一张图，因此**失败即说明该模型当前不能读图**——
    这正是「搭配失败」「入库失败」最常见的原因。
    """
    name = str(payload.get("role", "planner")).strip()
    role = _ROLE_MAP.get(name)
    if role is None:
        raise HTTPException(
            status_code=422,
            detail=f"role 只能是 {sorted(_ROLE_MAP)}，收到 {name!r}",
        )

    try:
        client = get_client(role)
        model = get_model_name(role)
    except ModelConfigError as exc:
        return {
            "role": name,
            "ok": False,
            "vision_ok": None,
            "model": None,
            "ms": 0,
            "stage": "config",
            "error": str(exc),
        }

    started = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=_messages_for(role),  # type: ignore[arg-type]
            temperature=0,
            response_format={"type": "json_object"},
            timeout=TEST_TIMEOUT_SECONDS,
            **model_extra_kwargs(model),
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        content = ""
        if completion.choices:
            content = (completion.choices[0].message.content or "")[:200]
        # 视觉角色的图是纯红 (200,40,40)。接口 200 只说明「能连」，
        # 只有答出红/red 才说明「真的看到了图」——这两件事完全不同。
        vision_ok: bool | None = None
        if role != ModelRole.TEXT_PLANNER:
            lowered = content.lower()
            vision_ok = ("red" in lowered) or ("红" in lowered)
        return {
            "role": name,
            "ok": True,
            "vision_ok": vision_ok,
            "model": model,
            "ms": elapsed,
            "stage": "done",
            "sample": content,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 自检要把任何异常如实返回
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "role": name,
            "ok": False,
            "vision_ok": None,
            "model": model,
            "ms": elapsed,
            "stage": "call",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


# ---------------------------------------------------------------- 历史数据中文化
@router.post("/maintenance/zh-values")
async def rewrite_zh_values(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """把库里历史英文展示值改写成中文（幂等）。

    只动 subcategory / pattern / material_guess / colors[].name /
    description / style_tags / occasion_tags；category、attributes、
    seasons、formality、warmth 这些判定字段一个都不碰。
    """
    from app.services.zh_values import rewrite_english_values

    try:
        stats = await rewrite_english_values(session)
    except Exception as exc:  # noqa: BLE001 - 直接把原因返回给前端
        logger.exception("历史数据中文化失败")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"ok": True, "stats": stats}
