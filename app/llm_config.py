"""三个模型角色的统一配置入口（U4：模型与 Key 只从后端 .env 读）。

AIdrobe.md「模型配置原则」
--------------------------
- 代码中禁止硬编码任何模型名、base_url、api_key；全部从环境变量读。
- 所有模型调用走 OpenAI 兼容 Chat Completions 接口，按角色构造客户端。
- prompt 只描述任务，不含模型名/供应商名。

U4 裁决 3：废除前端 Header 传 Key
---------------------------------
旧版前端每次请求带 `X-*-API-Key`，由中间件写进 ContextVar，本模块优先
取请求覆盖值。现在所有配置统一存后端 `.env`，`GET/POST /api/settings`
是唯一读写入口；写入后调用 `clear_client_cache()`，不重启即可生效。

回退顺序（三个角色共用同一家供应商时只填一套即可）：
- base_url：角色专属变量 → `QWEN_BASE_URL`
- api_key：角色专属变量 → `DASHSCOPE_API_KEY`
- model：角色专属变量 → `QWEN_MODEL`

`validate_all_roles(require_keys=False)` 允许无 Key 启动：欢迎向导与
掉线提示由前端按 `has_valid_keys` 判断，启动阶段不该拦住用户。
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from enum import Enum
from pathlib import Path
from typing import Any, Final

from openai import AsyncOpenAI

logger: logging.Logger = logging.getLogger(__name__)

QWEN_BASE_URL_ENV: str = "QWEN_BASE_URL"
QWEN_MODEL_ENV: str = "QWEN_MODEL"
DASHSCOPE_API_KEY_ENV: str = "DASHSCOPE_API_KEY"


class ModelRole(str, Enum):
    """三个模型角色；角色与供应商解耦，可指向同一家也可完全不同。"""

    VISION_EXTRACTOR = "VISION_EXTRACTOR"
    TEXT_PLANNER = "TEXT_PLANNER"
    MULTIMODAL_STYLIST = "MULTIMODAL_STYLIST"


class ModelConfigError(RuntimeError):
    """某个角色的配置缺失或非法；消息里包含具体变量名/前端提示。"""


class ImageBlockError(RuntimeError):
    """图片读取/编码失败；消息里包含具体路径。"""


# 每个角色必填的三个变量名。IMAGE_FORMAT 有默认值，不算必填。
_ROLE_REQUIRED: Final[dict[ModelRole, dict[str, str]]] = {
    ModelRole.VISION_EXTRACTOR: {
        "base_url": "VISION_BASE_URL",
        "api_key": "VISION_API_KEY",
        "model": "VISION_MODEL",
    },
    ModelRole.TEXT_PLANNER: {
        "base_url": "PLANNER_BASE_URL",
        "api_key": "PLANNER_API_KEY",
        "model": "PLANNER_MODEL",
    },
    ModelRole.MULTIMODAL_STYLIST: {
        "base_url": "STYLIST_BASE_URL",
        "api_key": "STYLIST_API_KEY",
        "model": "STYLIST_MODEL",
    },
}

_ROLE_IMAGE_FORMAT: Final[dict[ModelRole, str]] = {
    ModelRole.VISION_EXTRACTOR: "VISION_IMAGE_FORMAT",
    ModelRole.MULTIMODAL_STYLIST: "STYLIST_IMAGE_FORMAT",
}

# 缓存键是 (base_url, api_key)：同一请求内多个调用复用一个客户端；
# 不同 Key / 不同供应商不会互相串用。
_CLIENT_CACHE: dict[tuple[str, str], AsyncOpenAI] = {}


def _resolve(role: ModelRole, key: str) -> str:
    """角色专属变量优先，其次 Qwen 通用变量；都没有返回空串。"""
    name: str = _ROLE_REQUIRED[role][key]
    value: str = os.environ.get(name, "").strip()
    if value:
        return value
    if key == "base_url":
        return os.environ.get(QWEN_BASE_URL_ENV, "").strip()
    if key == "api_key":
        return os.environ.get(DASHSCOPE_API_KEY_ENV, "").strip()
    if key == "model":
        return os.environ.get(QWEN_MODEL_ENV, "").strip()
    return ""


def get_client(role: ModelRole) -> AsyncOpenAI:
    """按角色返回 OpenAI 兼容异步客户端。

    U4 裁决 3：Key 与模型一律只从后端 `.env` 读，前端不再传 Header。
    同一 (base_url, api_key) 复用客户端；配置写入后由
    `clear_client_cache()` 清缓存，下一次调用即用新值。
    """
    role = ModelRole(role)
    base_url: str = _resolve(role, "base_url")
    api_key: str = _resolve(role, "api_key")

    if not base_url:
        raise ModelConfigError(
            f"模型角色 {role.value} 缺少 base_url。请配置 "
            f"{_ROLE_REQUIRED[role]['base_url']} 或 {QWEN_BASE_URL_ENV}。"
        )
    if not api_key:
        raise ModelConfigError(
            f"模型角色 {role.value} 缺少 API Key。请在设置页填写，"
            f"或配置 {_ROLE_REQUIRED[role]['api_key']} / "
            f"{DASHSCOPE_API_KEY_ENV}。"
        )

    cache_key = (base_url, api_key)
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    _CLIENT_CACHE[cache_key] = client
    return client


def get_model_name(role: ModelRole) -> str:
    """按角色返回模型名：角色专属变量优先，其次 QWEN_MODEL。"""
    role = ModelRole(role)
    value: str = _resolve(role, "model")
    if not value:
        raise ModelConfigError(
            f"模型角色 {role.value} 缺少 model。请配置 "
            f"{_ROLE_REQUIRED[role]['model']} 或 {QWEN_MODEL_ENV}。"
        )
    return value


def get_image_format(role: ModelRole) -> str:
    """返回图片传入格式；未配置时回退到 data_url（AIdrobe.md 的默认值）。"""
    role = ModelRole(role)
    name: str | None = _ROLE_IMAGE_FORMAT.get(role)
    if name is None:
        return "data_url"
    value: str = os.environ.get(name, "").strip() or "data_url"
    if value not in ("data_url", "url"):
        raise ModelConfigError(
            f"环境变量 {name} 只能是 data_url 或 url，实际为 {value!r}"
        )
    return value


def build_image_block(
    path_or_url: str, image_format: str, *, max_edge: int | None = None
) -> dict[str, Any]:
    """构造 Chat Completions 的 image_url 内容块。

    - data_url：读本地文件转 base64，返回 data:image/...;base64,... 形式。
    - url：直接把地址交给模型（要求该地址可被模型服务访问）。
    """
    if image_format not in ("data_url", "url"):
        raise ImageBlockError(
            f"不支持的 image_format={image_format!r}；只允许 data_url 或 url"
        )
    if image_format == "url":
        return {"type": "image_url", "image_url": {"url": path_or_url}}

    path = Path(path_or_url)
    try:
        payload: bytes = path.read_bytes()
    except OSError as exc:
        raise ImageBlockError(
            f"读取图片失败：{path_or_url}（{type(exc).__name__}: {exc}）"
        ) from exc
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/jpeg"
    if max_edge is not None and image_format == "data_url":
        # 搭配链路一次要传多张图；原图 base64 动辄 1~2MB，网关容易直接断连。
        # 缩到 max_edge 内能把总体积压到几分之一，视觉判断仍然够用。
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(payload)).convert("RGB")
        image.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=75)
        payload = buffer.getvalue()
        mime = "image/jpeg"
    encoded: str = base64.b64encode(payload).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
    }


def validate_all_roles(require_keys: bool = True) -> None:
    """启动时校验三角色配置。

    - `require_keys=True`（默认，M1 行为）：base_url / api_key / model 都必须在
      环境变量里，缺失时一次性列出全部变量名。
    - `require_keys=False`（M7 启动用）：只要求 base_url / model 可解析；
      Key 允许为空（前端会输入）。一个 Key 都没有时只 warning，不阻止启动。
    """
    missing: list[str] = []
    for role, mapping in _ROLE_REQUIRED.items():
        for key, name in mapping.items():
            if key == "api_key" and not require_keys:
                continue
            if not _resolve(role, key):
                missing.append(f"{role.value}.{name}")
    if missing:
        raise ModelConfigError(
            "以下模型配置环境变量缺失：" + "、".join(missing)
        )
    if not require_keys and not any(
        _resolve(role, "api_key") for role in _ROLE_REQUIRED
    ):
        logger.warning(
            "未配置任何模型 API Key；请在设置页填写后保存到 .env，"
            "或直接配置 %s。",
            DASHSCOPE_API_KEY_ENV,
        )


def clear_client_cache() -> None:
    """清空客户端缓存：配置写入 .env 后调用，不重启即生效（U4 裁决 3）。

    测试也用它隔离用例之间的环境变量。
    """
    _CLIENT_CACHE.clear()


# Qwen / OpenAI 兼容网关在 `response_format={"type":"json_object"}` 时，
# 要求任一 message 里出现 "json" 这个字符串，否则直接 400：
#   'messages' must contain the word 'json' in some form
# 我们的 prompt 全中文，所以必须在 system 里补一句英文提示。
# 四个调用点统一拼接这个常量，避免各写各的、再漏一个。
JSON_MODE_HINT: str = (
    "\n\nOutput format: reply with one valid JSON object only "
    "(json), no markdown fences, no extra prose."
)


# --------------------------------------------------------- 按模型适配请求参数
# 各供应商的私有参数不写死在业务代码里，统一在这里按模型名派生。
# 用 `extra_body` 传递，避免 OpenAI SDK 因不认识关键字而抛 TypeError。
def model_extra_kwargs(model: str) -> dict[str, Any]:
    """返回该模型专属的额外请求参数，可直接 `**` 展开进 create()。

    - `qwen3.8-omni-flash`：全模态输入但只输出文本，需要 `modalities=["text"]`。
    - `deepseek-flash`（官方 API）：开启思考，需要 `reasoning_effort=high` 与
      `thinking={"type": "enabled"}`。
    - 其它模型（含百炼网关的 `deepseek-v4.1-flash`、`qwen3.7-flash`）无额外参数。
      特意用精确匹配，不再靠名字里含 "deepseek" 就替用户加参数。
    """
    lowered: str = (model or "").strip().lower()
    extra: dict[str, Any] = {}
    if lowered == "qwen3.8-omni-flash":
        extra["modalities"] = ["text"]
    if lowered == "deepseek-flash":
        extra["reasoning_effort"] = "high"
        extra["thinking"] = {"type": "enabled"}
    return {"extra_body": extra} if extra else {}
