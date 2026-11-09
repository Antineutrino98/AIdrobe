"""U4 模块三：大模型调用的统一错误码。

业务代码只抛 `ModelServiceError(code=...)`，由 `app/main.py` 的全局异常
处理器转成结构化 JSON；前端 Fetch 拦截器按 `code` 给友好提示，
绝不把红色堆栈或供应商原始报错直接渲染给用户。
"""
from __future__ import annotations

MODEL_AUTH_OR_QUOTA_ERROR: str = "MODEL_AUTH_OR_QUOTA_ERROR"
MODEL_RESPONSE_ERROR: str = "MODEL_RESPONSE_ERROR"

FRIENDLY_MESSAGES: dict[str, str] = {
    MODEL_AUTH_OR_QUOTA_ERROR: "你的 AI API Key 已失效或余额不足，请检查设置",
    MODEL_RESPONSE_ERROR: "造型师今天灵感枯竭，请再试一次",
}


class ModelServiceError(RuntimeError):
    """带稳定错误码的模型调用异常。"""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        if code not in FRIENDLY_MESSAGES:
            raise ValueError(f"未知模型错误码 {code!r}")
        # 业务代码直接 raise ModelServiceError(CODE) 时也要拿到正确状态码。
        self.code = code
        self.status = status if status is not None else (
            401 if code == MODEL_AUTH_OR_QUOTA_ERROR else 502
        )
        super().__init__(FRIENDLY_MESSAGES[code])


def classify_model_exception(exc: BaseException) -> ModelServiceError:
    """把 SDK / 解析 / 超时异常归到两个稳定错误码。

    401/402/403、以及网关常见的配额关键词 → 鉴权或余额问题；
    超时、连接失败、JSON 解析失败、空 choices 等 → 响应问题。
    """
    status = getattr(exc, "status_code", None)
    lowered = str(exc).lower()
    auth_quota = status in (401, 402, 403) or any(
        word in lowered
        for word in ("insufficient_quota", "invalid_api_key", "unauthorized", "quota")
    )
    if auth_quota:
        return ModelServiceError(MODEL_AUTH_OR_QUOTA_ERROR, status=401)
    return ModelServiceError(MODEL_RESPONSE_ERROR, status=502)
