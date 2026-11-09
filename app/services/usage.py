"""U4 模块三：一次推荐里各次模型调用的 token 用量收集。

显式传参、不用 ContextVar：裁决 3 刚废除的 Header 传 Key 用的就是
ContextVar，这里避免混淆。调用方创建 collector，传给 Planner / Stylist，
推荐结束时 `to_json()` 一次性写进 `outfit_sessions.token_usage`。

模型没返回 usage（部分网关会省略）时记为 reported=false、数值 0，
不让它把整次推荐搞失败。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class UsageCollector:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, role: str, completion: Any) -> None:
        """从 OpenAI 兼容的 ChatCompletion 响应里提取 usage。"""
        usage = getattr(completion, "usage", None)
        entry: dict[str, Any] = {
            "role": role,
            "reported": usage is not None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        if usage is not None:
            entry["prompt_tokens"] = _int(getattr(usage, "prompt_tokens", 0))
            entry["completion_tokens"] = _int(getattr(usage, "completion_tokens", 0))
            entry["total_tokens"] = _int(getattr(usage, "total_tokens", 0))
            # 少数网关只给 total，不给分项。
            if not entry["total_tokens"]:
                entry["total_tokens"] = (
                    entry["prompt_tokens"] + entry["completion_tokens"]
                )
        self.calls.append(entry)

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": list(self.calls),
            "prompt_tokens": sum(c["prompt_tokens"] for c in self.calls),
            "completion_tokens": sum(c["completion_tokens"] for c in self.calls),
            "total_tokens": sum(c["total_tokens"] for c in self.calls),
        }
