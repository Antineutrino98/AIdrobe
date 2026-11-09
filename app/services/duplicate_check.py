"""M4 流程 A：购前查重 + M4 三个流程共用的 TEXT_PLANNER 调用器。

职责边界
--------
- 只回答「这件候选和衣橱里已有的有多像」，**不给购买建议**（那是流程 C）。
- 查重解释用 TEXT_PLANNER；模型名/密钥只从 llm_config 读，代码不写死。
- 候选 embedding 为空时尝试补生成（复用 M2 的 generate_embedding），
  仍失败标记 duplicate_check="unavailable"，不阻断后续搭配验证。

关于 call_text_planner
----------------------
M4 有三个地方要用 TEXT_PLANNER（查重解释、综合决策、缺口总结）。
验收 5 要求「LLM 输出合法 JSON，失败重试并降 temperature」，所以统一放这里：
第一次 temperature=0.2，解析/校验失败后第二次降到 0.0，仍失败抛 PlannerCallError。
不新建额外 service 文件，另外两个模块直接 import 本函数。
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.llm_config import (
    model_extra_kwargs,
    JSON_MODE_HINT, ModelConfigError, ModelRole, get_client, get_model_name,
)
from app.models import EMBEDDING_DIM
from app.prompts import load_prompt
from app.schemas import GarmentSchema, SimilarGarmentItem
from app.services.embedding import generate_embedding
from app.services.ingest import image_dir

logger: logging.Logger = logging.getLogger(__name__)

# users.settings 里的两个键名（M4.md 只说「从用户 settings 读」，没给键名）。
STRICTNESS_KEY: str = "strictness"
THRESHOLD_KEY: str = "duplicate_threshold"
DEFAULT_THRESHOLD_CONSERVATIVE: float = 0.82
DEFAULT_THRESHOLD_PERMISSIVE: float = 0.75
SIMILAR_LIMIT: int = 5

SIMILARITY_LEVELS: frozenset[str] = frozenset(
    {"duplicate", "near_duplicate", "similar"}
)
RISK_LEVELS: tuple[str, ...] = ("none", "low", "medium", "high")


class PlannerConfigError(RuntimeError):
    """TEXT_PLANNER 环境变量没配好；重试没有意义。"""


class PlannerCallError(RuntimeError):
    """两次调用都没能拿到合法 JSON。"""


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _extract_text(choice: Any) -> str:
    content = choice.message.content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return str(content or "")


async def call_text_planner(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    temperatures: tuple[float, ...] = (0.2, 0.0),
    role: ModelRole = ModelRole.TEXT_PLANNER,
) -> dict[str, Any]:
    """调用文本模型返回 JSON 对象；失败重试一次并降低 temperature。

    `role` 允许「买不买 / 缺口」复用搭配决策的模型（用户要求）。

    M4.md 验收 5。temperature 序列由高到低，测试会断言第二次确实更小。
    """
    try:
        client = get_client(role)
        model = get_model_name(role)
    except ModelConfigError as exc:
        raise PlannerConfigError(str(exc)) from exc

    last_error: Exception | None = None
    for attempt, temperature in enumerate(temperatures, start=1):
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
            return _parse_json(_extract_text(completion.choices[0]))
        except Exception as exc:  # noqa: BLE001 - 网络/解析/校验失败都要重试
            last_error = exc
            logger.warning(
                "TEXT_PLANNER 第 %d/%d 次失败（temperature=%.2f）：%s",
                attempt, len(temperatures), temperature, exc,
            )
    raise PlannerCallError("文本模型两次都没有返回合法 JSON") from last_error


def resolve_strictness(
    settings: dict[str, Any] | None, override: str | None = None
) -> str:
    """strictness：请求显式传值优先，其次 users.settings，再读环境变量默认值。

    M7.md 的默认值写的是 `moderate`，但 M4 冻结的取值只有
    `conservative` / `permissive`；`moderate` 在这里映射为 `conservative`，
    避免为了一个默认值改动 M4 已冻结的枚举。
    """
    raw = (
        override
        or (settings or {}).get(STRICTNESS_KEY)
        or os.environ.get("DEFAULT_STRICTNESS")
    )
    if raw == "moderate":
        raw = "conservative"
    return raw if raw in ("conservative", "permissive") else "conservative"


def resolve_threshold(settings: dict[str, Any] | None, strictness: str) -> float:
    """查重阈值：settings.duplicate_threshold 优先，否则按严格度取默认值。"""
    default = (
        DEFAULT_THRESHOLD_PERMISSIVE
        if strictness == "permissive"
        else DEFAULT_THRESHOLD_CONSERVATIVE
    )
    raw = (settings or {}).get(THRESHOLD_KEY)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是数字，使用默认阈值 %.2f", THRESHOLD_KEY, raw, default)
        return default
    if not 0.0 <= value <= 1.0:
        logger.warning("%s=%r 超出 0~1，使用默认阈值 %.2f", THRESHOLD_KEY, raw, default)
        return default
    return value


def garment_profile(garment: GarmentSchema) -> dict[str, Any]:
    """送进文本模型的单品摘要（不含 512 维向量，避免撑爆 prompt）。"""
    description: Any = None
    if isinstance(garment.vision_raw, dict):
        description = garment.vision_raw.get("description")
    # colors 是 list[ColorSpec]（Pydantic 对象），必须先转成普通 dict，
    # 否则 json.dumps 整个 payload 时会抛 TypeError（mock 测试看不出来）。
    colors = [
        item.model_dump() if hasattr(item, "model_dump") else item
        for item in garment.colors
    ]
    return {
        "id": str(garment.id),
        "category": garment.category,
        "subcategory": garment.subcategory,
        "colors": colors,
        "material_guess": garment.material_guess,
        "formality": garment.formality,
        "seasons": garment.seasons,
        "style_tags": garment.style_tags,
        "occasion_tags": garment.occasion_tags,
        "warmth": garment.warmth,
        "pattern": garment.pattern,
        "attributes": garment.attributes,
        "description": description,
    }


@dataclass
class DuplicateResult:
    check: str = "unavailable"          # ok / no_match / unavailable
    risk: str = "none"                  # high / medium / low / none
    threshold: float = DEFAULT_THRESHOLD_CONSERVATIVE
    strictness: str = "conservative"
    matches: list[dict[str, Any]] = field(default_factory=list)
    similar_items: list[SimilarGarmentItem] = field(default_factory=list)
    existing_similar_ids: list[str] = field(default_factory=list)


def derive_risk(matches: list[dict[str, Any]]) -> str:
    """按最严重的一条命中定级；LLM 不能把规则算出的高风险改低。"""
    levels = {item.get("similarity_level") for item in matches}
    if "duplicate" in levels:
        return "high"
    if "near_duplicate" in levels:
        return "medium"
    if "similar" in levels:
        return "low"
    return "none"


def _fallback_level(similarity: float) -> str:
    if similarity >= 0.92:
        return "duplicate"
    if similarity >= 0.82:
        return "near_duplicate"
    return "similar"


def clean_matches(
    raw: dict[str, Any], known: dict[uuid.UUID, float]
) -> list[dict[str, Any]]:
    """只保留命中集合内的 id；非法字段丢弃（与 M3 的清洗原则一致）。"""
    raw_matches = raw.get("matches")
    if not isinstance(raw_matches, list):
        raise ValueError("matches 不是数组")
    result: list[dict[str, Any]] = []
    for item in raw_matches:
        if not isinstance(item, dict):
            continue
        try:
            garment_id = uuid.UUID(str(item.get("existing_garment_id", "")))
        except (ValueError, TypeError):
            continue
        if garment_id not in known:
            logger.warning("丢弃越界 existing_garment_id=%s", garment_id)
            continue
        level = item.get("similarity_level")
        if level not in SIMILARITY_LEVELS:
            logger.warning("丢弃非法 similarity_level=%r", level)
            continue
        result.append(
            {
                "existing_garment_id": str(garment_id),
                "similarity_level": level,
                "key_differences": str(item.get("key_differences") or ""),
                "same_scenarios": _str_list(item.get("same_scenarios")),
                "different_scenarios": _str_list(item.get("different_scenarios")),
                "similarity": round(known[garment_id], 6),
            }
        )
    return result


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


async def ensure_candidate_embedding(
    candidate: GarmentSchema, garments: Any
) -> GarmentSchema | None:
    """embedding 为空时补生成并回写；失败返回 None（标记 unavailable）。"""
    if candidate.embedding and len(candidate.embedding) == EMBEDDING_DIM:
        return candidate
    path = image_dir() / f"{candidate.id}.jpg"
    if not path.is_file():
        logger.warning("候选 %s 没有本地图片，无法补生成 embedding", candidate.id)
        return None
    try:
        vector = await generate_embedding(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 - 补生成失败必须降级
        logger.warning("补生成 embedding 失败：%s", exc)
        return None
    if not vector or len(vector) != EMBEDDING_DIM:
        logger.warning("补生成的 embedding 维度不符，标记 unavailable")
        return None
    updater = getattr(garments, "update_embedding", None)
    if updater is not None:
        try:
            await updater(candidate.id, vector)
        except Exception as exc:  # noqa: BLE001 - 回写失败不影响本次分析
            logger.warning("embedding 回写失败（本次仍使用内存值）：%s", exc)
    return candidate.model_copy(update={"embedding": vector})


async def run_duplicate_check(
    *,
    candidate: GarmentSchema,
    similar: list[tuple[uuid.UUID, float]],
    garments: Any,
    settings: dict[str, Any] | None,
    strictness: str,
    threshold: float,
) -> DuplicateResult:
    """流程 A 主入口：命中 → TEXT_PLANNER 解释 → 结构化结果。"""
    result = DuplicateResult(
        check="ok", risk="none", threshold=threshold, strictness=strictness
    )
    if not similar:
        result.check = "no_match"
        return result

    # 取命中衣物的完整档案；查不到明细时仍保留 id 与相似度。
    known: dict[uuid.UUID, float] = {}
    profiles: list[dict[str, Any]] = []
    getter = getattr(garments, "get", None)
    for garment_id, score in similar[:SIMILAR_LIMIT]:
        known[garment_id] = float(score)
        profile: dict[str, Any] = {"id": str(garment_id), "similarity": round(float(score), 6)}
        if getter is not None:
            try:
                existing = await getter(garment_id)
            except Exception as exc:  # noqa: BLE001 - 明细读取失败不阻断
                logger.warning("读取相似衣物 %s 失败：%s", garment_id, exc)
                existing = None
            if existing is not None:
                profile.update(garment_profile(existing))
                result.similar_items.append(
                    SimilarGarmentItem(
                        garment_id=garment_id,
                        image_url=existing.image_url,
                        category=existing.category,
                        subcategory=existing.subcategory,
                        similarity=float(score),
                    )
                )
        profiles.append(profile)

    payload: dict[str, Any] = {
        "candidate": garment_profile(candidate),
        "existing_items": profiles,
        "similarity_threshold": threshold,
    }
    try:
        raw = await call_text_planner(
            load_prompt("duplicate"),
            payload,
            role=ModelRole.MULTIMODAL_STYLIST,
        )
        result.matches = clean_matches(raw, known)
    except (PlannerConfigError, PlannerCallError, ValueError) as exc:
        # 模型不可用时不能让整个评估崩掉：用相似度给出保守的分级解释。
        logger.warning("查重解释降级为规则分级：%s", exc)
        result.matches = [
            {
                "existing_garment_id": str(garment_id),
                "similarity_level": _fallback_level(score),
                "key_differences": "未能获取模型解释，仅依据向量相似度判断。",
                "same_scenarios": [],
                "different_scenarios": [],
                "similarity": round(score, 6),
            }
            for garment_id, score in known.items()
        ]

    # 模型漏报的命中项仍要出现在结果里，避免「明明有重复却说不重复」。
    covered = {item["existing_garment_id"] for item in result.matches}
    for garment_id, score in known.items():
        if str(garment_id) not in covered:
            result.matches.append(
                {
                    "existing_garment_id": str(garment_id),
                    "similarity_level": _fallback_level(score),
                    "key_differences": "模型未逐件说明，按向量相似度归类。",
                    "same_scenarios": [],
                    "different_scenarios": [],
                    "similarity": round(score, 6),
                }
            )

    by_id = {str(gid): score for gid, score in known.items()}
    for item in result.matches:
        item["similarity"] = round(by_id.get(item["existing_garment_id"], 0.0), 6)
    result.risk = derive_risk(result.matches)
    result.existing_similar_ids = [str(gid) for gid, _ in similar[:SIMILAR_LIMIT]]

    # 把模型给的解释回填到前端要展示的 similar_items 上。
    explanations = {item["existing_garment_id"]: item for item in result.matches}
    for entry in result.similar_items:
        detail = explanations.get(str(entry.garment_id))
        if detail:
            entry.similarity_level = detail["similarity_level"]
            entry.key_differences = detail["key_differences"]
    return result
