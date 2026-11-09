"""U4 模块二：模型配置的唯一读写入口（后端 `.env`）。

裁决 3 之后前端不再用请求头传 Key，`GET/POST /api/settings` 是唯一来源：
- `GET` 返回脱敏后的三角色配置 + `has_garments` + `has_valid_keys`；
- `POST` 只接受白名单键，带 `*` 的脱敏值一律跳过（裁决 4，防止把真 Key
  覆盖成 `sk-1****abcd`），写完后同步 `os.environ` 并 `clear_client_cache()`，
  不重启进程即可生效（裁决 3 的热重载）。

`.env` 写入用「改写行 + 原子替换」，保留注释、顺序和未涉及的配置项。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BASE_DIR
from app.deps import get_db
from app.llm_config import clear_client_cache
from app.models import Garment, OutfitSession

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["settings"])

# 角色 → `.env` 变量名。只暴露这三组，避免前端能写任意环境变量。
ROLE_ENV_KEYS: dict[str, dict[str, str]] = {
    "vision": {
        "base_url": "VISION_BASE_URL",
        "api_key": "VISION_API_KEY",
        "model": "VISION_MODEL",
    },
    "planner": {
        "base_url": "PLANNER_BASE_URL",
        "api_key": "PLANNER_API_KEY",
        "model": "PLANNER_MODEL",
    },
    "stylist": {
        "base_url": "STYLIST_BASE_URL",
        "api_key": "STYLIST_API_KEY",
        "model": "STYLIST_MODEL",
    },
}

_ALLOWED_KEYS: frozenset[str] = frozenset(
    name
    for mapping in ROLE_ENV_KEYS.values()
    for name in mapping.values()
) | {
    "QWEN_BASE_URL", "QWEN_MODEL", "DASHSCOPE_API_KEY",
    "VISION_IMAGE_FORMAT", "STYLIST_IMAGE_FORMAT",
}

# 隔离测试时可用 AIDROBE_ENV_FILE 指向副本，保证绝不写用户的真实 .env。
_ENV_PATH: Path = Path(os.environ.get("AIDROBE_ENV_FILE", str(BASE_DIR / ".env")))
_ENV_TMP_PATH: Path = _ENV_PATH.with_name(_ENV_PATH.name + ".u4tmp")


class RoleSettings(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class SettingsPayload(BaseModel):
    roles: dict[str, RoleSettings] = Field(default_factory=dict)
    # 老用户迁移：只想一次性写入通用 Key 时用这个。
    common_api_key: str | None = None


def mask_key(value: str) -> str:
    """只显示前后 4 位；带 `*` 便于 POST 时识别为「未修改」。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _resolved(role_keys: dict[str, str], field: str) -> str:
    """与 llm_config._resolve 相同的回退顺序，但这里只用于展示。"""
    value = os.environ.get(role_keys[field], "").strip()
    if value:
        return value
    fallback = {
        "base_url": "QWEN_BASE_URL",
        "api_key": "DASHSCOPE_API_KEY",
        "model": "QWEN_MODEL",
    }[field]
    return os.environ.get(fallback, "").strip()


def has_valid_keys() -> bool:
    """三个必需角色的 Key 是否都能解析（通用 DASHSCOPE_API_KEY 也算）。"""
    return all(_resolved(keys, "api_key") for keys in ROLE_ENV_KEYS.values())


async def count_garments(session: AsyncSession) -> int:
    result = await session.execute(sa.select(sa.func.count(Garment.id)))
    return int(result.scalar_one() or 0)


def write_env(updates: dict[str, str]) -> list[str]:
    """安全写入 `.env`：只动白名单键，保留注释与顺序，原子替换。

    返回真正改动过的键名，便于日志与测试断言。
    """
    unexpected = set(updates) - _ALLOWED_KEYS
    if unexpected:
        raise ValueError(f"不允许写入的配置项：{sorted(unexpected)}")

    original = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    lines = original.splitlines(keepends=True)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}\n")

    _ENV_TMP_PATH.write_text("".join(out), encoding="utf-8")
    os.replace(_ENV_TMP_PATH, _ENV_PATH)

    # `.env` 只在启动时被 load_dotenv 读一次；不更新 os.environ 的话
    # get_client() 仍会拿旧 Key，热重载会「假成功」。
    for key, value in updates.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    return sorted(updates)


@router.get("/settings")
async def read_settings(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """脱敏配置 + 向导/掉线判断所需的两个布尔值。"""
    roles: dict[str, Any] = {}
    for name, keys in ROLE_ENV_KEYS.items():
        key = _resolved(keys, "api_key")
        roles[name] = {
            "base_url": _resolved(keys, "base_url"),
            "model": _resolved(keys, "model"),
            "api_key": mask_key(key),
            "has_key": bool(key),
        }
    garments = await count_garments(session)
    return {
        "roles": roles,
        "has_garments": garments > 0,
        "has_valid_keys": has_valid_keys(),
    }


@router.post("/settings")
async def update_settings(payload: SettingsPayload) -> dict[str, Any]:
    """写入 `.env` 并立即热重载客户端缓存。"""
    updates: dict[str, str] = {}
    skipped: list[str] = []

    for name, cfg in payload.roles.items():
        if name not in ROLE_ENV_KEYS:
            raise HTTPException(
                status_code=422,
                detail=f"未知角色 {name!r}，只能是 {sorted(ROLE_ENV_KEYS)}",
            )
        for field in ("base_url", "model", "api_key"):
            value = getattr(cfg, field)
            if value is None:
                continue
            value = value.strip()
            # 裁决 4：带 * 的是 GET 返回的脱敏值，绝不能覆盖真 Key。
            if "*" in value:
                skipped.append(f"{name}.{field}")
                continue
            updates[ROLE_ENV_KEYS[name][field]] = value

    if payload.common_api_key is not None:
        value = payload.common_api_key.strip()
        if "*" in value:
            skipped.append("common_api_key")
        else:
            updates["DASHSCOPE_API_KEY"] = value

    changed: list[str] = []
    if updates:
        changed = write_env(updates)
        # 裁决 3：清掉客户端缓存，下一次调用即用新配置，无需重启。
        clear_client_cache()
        logger.info("配置已更新并热重载：%s", ", ".join(changed))

    return {
        "ok": True,
        "changed": changed,
        "skipped_masked": skipped,
        "has_valid_keys": has_valid_keys(),
    }


@router.get("/usage")
async def read_usage(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """今日总调用次数、生成套数与 token 消耗（U4 模块三）。

    01:30 反馈：本地已经 10 号，统计还接着 9 号累计。原因是 created_at 是
    timestamptz（数据库 now()，UTC），而数据库的当日日期函数按会话时区取值，
    UTC 尚未跨天时就会把昨天算进来。这里在 Python 里按本地 GMT+8 算出
    今天的起止时刻，再换算成 UTC 做范围查询，和前端日期显示保持一致。
    """
    local_tz = timezone(timedelta(hours=8))
    start_local = datetime.combine(
        datetime.now(local_tz).date(), time.min, tzinfo=local_tz
    )
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc)
    result = await session.execute(
        sa.select(
            OutfitSession.token_usage,
            OutfitSession.recommendations,
        ).where(
            OutfitSession.created_at >= start_utc,
            OutfitSession.created_at < end_utc,
        )
    )
    rows = result.all()

    def _number(payload: Any, key: str) -> int:
        value = (payload or {}).get(key, 0) if isinstance(payload, dict) else 0
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    outfit_count = 0
    calls = 0
    for payload, recommendations in rows:
        for key in tokens:
            tokens[key] += _number(payload, key)
        calls += len((payload or {}).get("calls", [])) if isinstance(payload, dict) else 0
        if isinstance(recommendations, list):
            outfit_count += len(recommendations)

    return {
        "sessions": len(rows),
        "outfit_count": outfit_count,
        "calls": calls,
        **tokens,
    }
