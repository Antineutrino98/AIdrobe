"""全局约束策略生成（U1）。

职责边界
--------
TEXT_PLANNER 只输出当天的**全局约束**（正式度区间、保暖度区间、叠穿提示、
风格方向、注意事项）。它不选衣物、不指定品类、更不指定任何槽位。

U1 重构前这里输出 `slots: [{role, conditions}]`，再由 filter 按槽位分桶检索，
等于让一个纯文本模型替搭配模型决定了「必须有几件上装几件下装」。
现在只给范围，具体组合完全交给 MULTIMODAL_STYLIST 在同一个混合候选池里决定。

校验规则
--------
- 解析失败 / 校验失败 → 重试一次（temperature=0.2）。
- 区间一律夹到 1..5，且保证 min <= max；非法值回落到安全默认。
- 中文文本字段截到 120 字，防止模型写长文浪费 token。
- 不做计划缓存（M3 明确要求）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from app.errors import (
    MODEL_AUTH_OR_QUOTA_ERROR,
    ModelServiceError,
    classify_model_exception,
)
from app.services.usage import UsageCollector
from app.llm_config import (
    JSON_MODE_HINT,
    ModelConfigError,
    ModelRole,
    get_client,
    get_model_name,
    model_extra_kwargs,
)
from app.prompts import (
    build_retrieval_plan_system_prompt,
    format_preference_block,
)

logger: logging.Logger = logging.getLogger(__name__)

LEVEL_MIN: int = 1
LEVEL_MAX: int = 5
TEXT_LIMIT: int = 120


class PlannerConfigError(RuntimeError):
    """TEXT_PLANNER 的环境变量没配好；重试没有意义。"""


class PlannerError(RuntimeError):
    """两次都没能生成合法计划。"""


class LevelRange(BaseModel):
    """1-5 的闭区间。"""

    min: int = LEVEL_MIN
    max: int = LEVEL_MAX


class RetrievalPlan(BaseModel):
    """当天的全局约束策略。没有任何槽位、品类或必选件数字段。"""

    layering_hint: str = ""
    formality_range: LevelRange = Field(default_factory=LevelRange)
    warmth_range: LevelRange = Field(default_factory=LevelRange)
    style_direction: str = ""
    avoid_notes: str = ""


DEFAULT_PLAN: RetrievalPlan = RetrievalPlan()


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:TEXT_LIMIT]


def _level(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(LEVEL_MIN, min(LEVEL_MAX, number))


def _range(value: Any) -> LevelRange:
    if not isinstance(value, dict):
        return LevelRange()
    low = _level(value.get("min"), LEVEL_MIN)
    high = _level(value.get("max"), LEVEL_MAX)
    if low > high:
        low, high = high, low
    return LevelRange(min=low, max=high)


def clean_plan(raw: dict[str, Any]) -> RetrievalPlan:
    """把模型输出清洗成全局约束策略；任何非法字段都回落到安全默认。"""
    return RetrievalPlan(
        layering_hint=_text(raw.get("layering_hint")),
        formality_range=_range(raw.get("formality_range")),
        warmth_range=_range(raw.get("warmth_range")),
        style_direction=_text(raw.get("style_direction")),
        avoid_notes=_text(raw.get("avoid_notes")),
    )


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("检索计划不是 JSON 对象")
    return data


def _extract_text(choice: Any) -> str:
    content = choice.message.content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return str(content or "")


async def _call_planner(
    system_prompt: str,
    user_payload: dict[str, Any],
    temperature: float,
    usage: UsageCollector | None = None,
) -> dict[str, Any]:
    try:
        client = get_client(ModelRole.TEXT_PLANNER)
        model = get_model_name(ModelRole.TEXT_PLANNER)
    except ModelConfigError as exc:
        raise ModelServiceError(MODEL_AUTH_OR_QUOTA_ERROR) from exc

    try:
        completion = await client.chat.completions.create(
            model=model,
            **model_extra_kwargs(model),
            messages=[
                {"role": "system", "content": system_prompt + JSON_MODE_HINT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        if usage is not None:
            usage.add("planner", completion)
        return _parse_json(_extract_text(completion.choices[0]))
    except Exception as exc:  # noqa: BLE001 - 统一映射成两个稳定错误码
        raise classify_model_exception(exc) from exc


async def plan_retrieval(
    *,
    scene: str,
    free_text: str | None,
    temperature: float | None,
    weather_condition: str | None,
    preference_text: str | None,
    category_counts: dict[str, int],
    dress_code_hint: str = "",
    temperature_min: float | None = None,
    temperature_max: float | None = None,
    cold_sensitivity: str = "normal",
    heat_sensitivity: str = "normal",
    usage: UsageCollector | None = None,
) -> RetrievalPlan:
    """生成并校验全局约束；失败重试一次（temperature=0.2）。"""
    system_prompt: str = build_retrieval_plan_system_prompt(
        category_counts, dress_code_hint
    )
    user_payload: dict[str, Any] = {
        "scene": scene,
        "free_text": free_text or "",
        "temperature_celsius": temperature,
        "temperature_min": temperature_min,
        "temperature_max": temperature_max,
        # 01:56：体感偏好，交给 Planner 调整 warmth_range / layering_hint。
        "cold_sensitivity": cold_sensitivity,
        "heat_sensitivity": heat_sensitivity,
        "weather_condition": weather_condition or "",
        # M5 Step 4：偏好按固定格式注入 user message；空文本用占位句。
        "user_preference": format_preference_block(preference_text),
    }

    last_error: Exception | None = None
    for attempt, temperature_setting in enumerate((0.0, 0.2), start=1):
        try:
            started = time.monotonic()
            raw = await _call_planner(
                system_prompt, user_payload, temperature_setting, usage
            )
            plan = clean_plan(raw)
            logger.info(
                "全局约束生成完成：%.1f 秒，正式度 %d-%d，保暖度 %d-%d",
                time.monotonic() - started,
                plan.formality_range.min,
                plan.formality_range.max,
                plan.warmth_range.min,
                plan.warmth_range.max,
            )
            return plan
        except ModelServiceError:
            raise  # 带错误码的模型异常不能被重试吞掉
        except PlannerConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - 解析/校验失败都要重试
            last_error = exc
            logger.warning("全局约束第 %d/2 次失败：%s", attempt, exc)
    raise PlannerError("全局约束生成失败，请稍后重试") from last_error
