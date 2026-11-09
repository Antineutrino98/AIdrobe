"""多模态搭配决策（U1 自由组合版本）。

职责边界
--------
把**一个混合候选池**连同真实图片交给 `MULTIMODAL_STYLIST`，由它自己决定
每套穿几件、怎么叠穿；本模块不做审美规则，也不预设任何槽位。

U1 重构前候选是按 slot 分桶发的，模型只能做「上装+下装+鞋」的填空题。
现在只有一个池子，模型可以给出 T恤+衬衫+外套、连衣裙+鞋、半身裙+衬裤
等任意组合。

硬约束
------
- 必须真的传图，让模型判断颜色/版型/纹理；
- `STYLIST_MAX_IMAGES` 控制图片张数，池子里其余单品只发文字档案；
- 模型返回的 garment_id 必须在池内，越界一律过滤；
- 一套搭配 ≥2 件即可，单件时须为连衣裙/套装这类一体式；
- 不再因「缺某个槽位」丢弃整套，也不允许调用方多轮重试。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.errors import (
    MODEL_AUTH_OR_QUOTA_ERROR,
    ModelServiceError,
    classify_model_exception,
)
from app.services.usage import UsageCollector
from app.llm_config import (
    model_extra_kwargs,
    JSON_MODE_HINT,
    ImageBlockError,
    ModelConfigError,
    ModelRole,
    build_image_block,
    get_client,
    get_image_format,
    get_model_name,
)
from app.prompts import format_preference_block, language_instruction
from app.schemas import GarmentSchema
from app.services.filter import (
    GROUP_CATEGORIES,
    ONE_PIECE_CATEGORIES,
    CandidatePool,
    category_group,
)
from app.services.ingest import image_dir

logger: logging.Logger = logging.getLogger(__name__)

STYLIST_MAX_IMAGES_ENV: str = "STYLIST_MAX_IMAGES"
STYLIST_IMAGE_MAX_TOKENS_ENV: str = "STYLIST_IMAGE_MAX_TOKENS"
DEFAULT_MAX_IMAGES: int = 6

# 品类大类只用于给模型返回的单品打标签，绝不用于分桶或校验。
LEGAL_GROUPS: frozenset[str] = frozenset(GROUP_CATEGORIES)

# reasoning 里出现这些词，视为「引用了视觉特征」（验收 8）。
VISUAL_REFERENCE_WORDS: tuple[str, ...] = (
    "颜色", "色", "配色", "色调", "版型", "剪裁", "轮廓", "廓形",
    "纹理", "面料", "材质", "图案", "印花", "条纹", "格子",
    "领口", "袖", "长度", "宽", "修身", "宽松", "挺括", "垂坠",
    "光泽", "厚度", "质感", "撞色", "同色",
)

_PROMPT_PATH: Path = Path(__file__).resolve().parents[1] / "prompts" / "stylist_ranking.md"


class StylistConfigError(RuntimeError):
    """MULTIMODAL_STYLIST 的环境变量没配好。"""


class StylistError(RuntimeError):
    """两次调用都没能返回可用的搭配。"""


class RecommendedItem(BaseModel):
    garment_id: uuid.UUID
    category: str | None = None
    slot: str | None = None


class OutfitRecommendation(BaseModel):
    style_direction: str
    items: list[RecommendedItem]
    reasoning: str
    match_score: int = Field(ge=1, le=10)


class StylistResult(BaseModel):
    recommendations: list[OutfitRecommendation]
    n_images: int
    model_used: str


@dataclass
class CandidateBlocks:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    candidate_ids: set[uuid.UUID] = field(default_factory=set)
    n_images: int = 0
    per_group: dict[str, int] = field(default_factory=dict)


def _max_images() -> int:
    raw: str = os.environ.get(STYLIST_MAX_IMAGES_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_IMAGES
    except ValueError:
        logger.warning("%s=%r 不是整数，使用默认 %d", STYLIST_MAX_IMAGES_ENV, raw, DEFAULT_MAX_IMAGES)
        value = DEFAULT_MAX_IMAGES
    return max(1, value)


def _garment_profile(garment: GarmentSchema) -> dict[str, Any]:
    description: Any = None
    if isinstance(garment.vision_raw, dict):
        description = garment.vision_raw.get("description")
    # colors 是 list[ColorSpec]（Pydantic 对象），转成普通 dict 才能 json.dumps。
    # M4 的候选单品带非空 colors，会走到这条路径。
    colors = [
        item.model_dump() if hasattr(item, "model_dump") else item
        for item in garment.colors
    ]
    # 搭配判断需要结构化的正式度/保暖度/场合标签；只看图会丢掉这些
    # 已经结构化好的信息。图片 + 这些字段一起给，可靠性最高。
    return {
        "id": str(garment.id),
        "category": garment.category,
        "subcategory": garment.subcategory,
        "colors": colors,
        "formality": garment.formality,
        "warmth": garment.warmth,
        "seasons": garment.seasons,
        "pattern": garment.pattern,
        "style_tags": garment.style_tags,
        "occasion_tags": garment.occasion_tags,
        "attributes": garment.attributes,
        "description": description,
    }


def build_candidate_blocks(
    pool: CandidatePool, max_images: int
) -> CandidateBlocks:
    """把一整个混合候选池按顺序发给模型，不做任何分桶。

    前 `max_images` 件附真实图片；其余只发文字档案并标注无图。
    这样既满足 U1「一个大池子、模型自由组合」，又不会让请求体爆炸。
    """
    result = CandidateBlocks()
    for index, garment in enumerate(pool.items):
        profile = _garment_profile(garment)
        profile["no_image"] = index >= max_images
        result.blocks.append(
            {
                "type": "text",
                "text": json.dumps(profile, ensure_ascii=False),
            }
        )
        if index < max_images:
            image_path = image_dir() / f"{garment.id}.jpg"
            if image_path.is_file():
                try:
                    # 本地图片只能用 base64；url 模式要求公网可达，这里不适用。
                    result.blocks.append(
                        build_image_block(str(image_path), "data_url", max_edge=640)
                    )
                    result.n_images += 1
                except ImageBlockError as exc:
                    logger.warning("候选 %s 图片读取失败：%s", garment.id, exc)
                    result.blocks.append({"type": "text", "text": "（这张无图）"})
            else:
                result.blocks.append({"type": "text", "text": "（这张无图）"})
        result.candidate_ids.add(garment.id)
        group = category_group(garment.category)
        result.per_group[group] = result.per_group.get(group, 0) + 1
    return result


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("搭配结果不是 JSON 对象")
    return data


def _extract_text(choice: Any) -> str:
    content = choice.message.content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return str(content or "")


def _infer_slot(category: str | None) -> str | None:
    """按品类推断大类标签，仅用于前端展示。"""
    return category_group(category) if category else None


# 模型偶尔会把分组写成别名或中文；这里只做归类，归类不准**不影响**搭配是否被采信。
_GROUP_ALIASES: dict[str, str] = {
    "top": "tops", "tops": "tops", "upper": "tops",
    "上装": "tops", "上衣": "tops", "内搭": "tops", "打底": "tops",
    "dress": "one_piece", "连衣裙": "one_piece", "连体裤": "one_piece",
    "one_piece": "one_piece", "onepiece": "one_piece", "套装": "one_piece",
    "下装": "bottoms", "裤子": "bottoms", "裙子": "bottoms", "bottoms": "bottoms",
    "外套": "outer", "大衣": "outer", "outer": "outer",
    "鞋": "shoes", "shoes": "shoes", "footwear": "shoes",
    "配饰": "accessory", "accessory": "accessory",
}


def _normalize_slot(raw_slot: Any, category: str | None) -> str | None:
    """归一化模型给的大类标签；只用于展示，不参与任何校验。"""
    raw = str(raw_slot or "").strip().lower()
    if raw in LEGAL_GROUPS:
        return raw
    if raw in _GROUP_ALIASES:
        return _GROUP_ALIASES[raw]
    return _infer_slot(category) or raw or None


def _range_dict(value: Any) -> dict[str, int]:
    if value is None:
        return {"min": 1, "max": 5}
    return {"min": getattr(value, "min", 1), "max": getattr(value, "max", 5)}


def _has_visual_reference(reasoning: str) -> bool:
    return any(word in reasoning for word in VISUAL_REFERENCE_WORDS)


def _is_valid_outfit(
    items: list[RecommendedItem], *, pool_has_lower: bool = False
) -> bool:
    """一套搭配是否成立 —— 只看「穿得通」，不看有没有凑齐固定几件。

    U1 废除了「每槽一件」的填空校验；U5 用户反馈补了一条常识兜底：
    多件套必须同时覆盖上半身和下半身，不能整套只有上装或只有下装。
    这条兜底是**条件式**的：只有候选池里确实存在下装 / 一体式时才启用，
    否则衣橱真的没下装时会导致一套都返回不了。

    - 一件一体式（连衣裙 / 连体裤 / 套装）可以单独成一套；
    - 否则至少 2 件；
    - 启用兜底时，多件套需同时有「下装或一体式」和「上装或外套或一体式」。
    """
    if not items:
        return False
    categories = {
        str(item.category).strip().lower()
        for item in items
        if item.category
    }
    if len(items) == 1:
        return bool(categories & ONE_PIECE_CATEGORIES)
    if pool_has_lower:
        groups = {
            category_group(str(item.category).strip().lower())
            for item in items
            if item.category
        }
        has_lower = bool(groups & {"bottoms", "one_piece"})
        has_upper = bool(groups & {"tops", "outer", "one_piece"})
        if not (has_lower and has_upper):
            logger.info("丢弃只覆盖半身的搭配：%s", sorted(groups))
            return False
    return True


def clean_recommendations(
    raw: dict[str, Any],
    candidate_ids: set[uuid.UUID],
    required_slots: set[str] | None = None,
    *,
    pool_has_lower: bool = False,
    shoe_items: list[RecommendedItem] | None = None,
) -> list[OutfitRecommendation]:
    """只丢弃「越界 id」和「不成立的搭配」，其余原样保留。

    以前这里会按固定的三件套逐格校验，缺一格就整批丢弃 ——
    这正是「模型永远只给上装下装鞋各一件」的根源：它学会了只要少一件就
    会被毙掉，于是不敢叠穿、不敢给连衣裙。现在校验只问一句：
    **这套穿得通吗**（见 `_is_valid_outfit`）。

    `required_slots` 仅为兼容旧调用保留，不再影响结果。
    """
    raw_items = raw.get("recommendations")
    if not isinstance(raw_items, list):
        return []

    result: list[OutfitRecommendation] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        items: list[RecommendedItem] = []
        for item in entry.get("items") or []:
            if not isinstance(item, dict):
                continue
            try:
                garment_id = uuid.UUID(str(item.get("garment_id", "")))
            except (ValueError, TypeError):
                continue
            if garment_id not in candidate_ids:
                logger.warning("丢弃越界 garment_id=%s", garment_id)
                continue
            category_raw = item.get("category")
            items.append(
                RecommendedItem(
                    garment_id=garment_id,
                    category=category_raw,
                    slot=_normalize_slot(
                        item.get("slot"),
                        str(category_raw) if category_raw else None,
                    ),
                )
            )
        if not _is_valid_outfit(items, pool_has_lower=pool_has_lower):
            logger.warning(
                "丢弃不成立的搭配 %r：%d 件，品类 %s",
                entry.get("style_direction"),
                len(items),
                "/".join(sorted({str(i.category) for i in items})),
            )
            continue

        # 用户 2026-11-09 定策：衣橱里有鞋就配上，没鞋就完全不提。
        # 模型偶尔会漏鞋，这里做确定性兜底，保证「有鞋」时结果里一定看得见鞋。
        # shoe_items 为空（池里没鞋）时什么都不做，也不会因为缺鞋拒绝这套。
        if shoe_items and not any(
            category_group(str(item.category or "").strip().lower()) == "shoes"
            for item in items
        ):
            used_ids = {item.garment_id for item in items}
            pick = next(
                (
                    shoe
                    for shoe in shoe_items[len(result) % len(shoe_items) :]
                    + shoe_items[: len(result) % len(shoe_items)]
                    if shoe.garment_id not in used_ids
                ),
                None,
            )
            if pick is not None:
                items.append(pick)
                logger.info("候选池有鞋但模型漏配，已补一双：%s", pick.garment_id)

        reasoning_raw = entry.get("reasoning")
        reasoning = reasoning_raw.strip() if isinstance(reasoning_raw, str) else ""
        try:
            score = int(entry.get("match_score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(1, min(10, score))
        if reasoning and not _has_visual_reference(reasoning):
            logger.warning("reasoning 未引用视觉特征，match_score 降 1：%s", reasoning[:60])
            score = max(1, score - 1)

        style_direction_raw = entry.get("style_direction")
        result.append(
            OutfitRecommendation(
                style_direction=(
                    style_direction_raw.strip()
                    if isinstance(style_direction_raw, str)
                    and style_direction_raw.strip()
                    else "未命名风格"
                ),
                items=items,
                reasoning=reasoning,
                match_score=score,
            )
        )
    return result


async def _call_stylist(
    blocks: list[dict[str, Any]], usage: UsageCollector | None = None
) -> dict[str, Any]:
    try:
        client = get_client(ModelRole.MULTIMODAL_STYLIST)
        model = get_model_name(ModelRole.MULTIMODAL_STYLIST)
    except ModelConfigError as exc:
        raise ModelServiceError(MODEL_AUTH_OR_QUOTA_ERROR) from exc

    prompt: str = _PROMPT_PATH.read_text(encoding="utf-8") + language_instruction()
    try:
        completion = await client.chat.completions.create(
            model=model,
            **model_extra_kwargs(model),
            messages=[
                {"role": "system", "content": prompt + JSON_MODE_HINT},
                {"role": "user", "content": blocks},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        if usage is not None:
            usage.add("stylist", completion)
        return _parse_json(_extract_text(completion.choices[0]))
    except Exception as exc:  # noqa: BLE001 - 统一映射成两个稳定错误码
        raise classify_model_exception(exc) from exc


async def rank_outfits(
    *,
    pool: CandidatePool,
    scene: str,
    temperature: float | None,
    weather_condition: str | None,
    temperature_min: float | None = None,
    temperature_max: float | None = None,
    cold_sensitivity: str = "normal",
    heat_sensitivity: str = "normal",
    required_garment_id: uuid.UUID | None = None,
    preference_text: str | None = None,
    plan: Any = None,
    usage: UsageCollector | None = None,
    outfit_count: int = 3,
) -> StylistResult:
    """调用多模态模型做搭配；网络/解析失败抛 StylistError。

    `required_garment_id` 是 M4 流程 B 新增的**可选**参数：非空时要求每一套
    搭配都包含这件候选单品，并过滤掉不含它的结果。M3 不传该参数，
    行为与原来完全一致（默认 None）。

    `preference_text` 是 M5 Step 4 的注入点：非空时按固定格式拼进 user
    message 的第一个文本块；为空时用 "No preference history yet."。
    """
    image_format: str = get_image_format(ModelRole.MULTIMODAL_STYLIST)
    if image_format != "data_url":
        logger.warning(
            "STYLIST_IMAGE_FORMAT=%s 不适用于本地图片，本次改用 data_url",
            image_format,
        )
    built: CandidateBlocks = build_candidate_blocks(pool, _max_images())
    if not built.candidate_ids:
        raise StylistError("候选池为空，无法生成搭配")

    # 用户 2026-11-09 定策：鞋不是必填槽位，取决于衣橱里到底有没有鞋。
    # 有鞋 → 每套都配上；没鞋 → 不要求、不编造、不因缺鞋判无效。
    # 判定与实际补鞋用同一套 category_group，避免 per_group 的键名差异漏判。
    shoe_items: list[RecommendedItem] = [
        RecommendedItem(
            garment_id=garment.id,
            category=str(getattr(garment, "category", "") or ""),
            slot="shoes",
        )
        for garment in pool.items
        if category_group(
            str(getattr(garment, "category", "") or "").strip().lower()
        )
        == "shoes"
    ]
    pool_has_shoes = bool(shoe_items)
    logger.info(
        "鞋策略：候选池 %d 件，其中鞋 %d 双，pool_has_shoes=%s",
        len(pool.items),
        len(shoe_items),
        pool_has_shoes,
    )

    # U1：把 Planner 给的全局约束原样交给搭配模型，而不是替它拆成槽位。
    context: dict[str, Any] = {
        "scene": scene,
        "layering_hint": getattr(plan, "layering_hint", "") or "",
        "style_direction": getattr(plan, "style_direction", "") or "",
        "avoid_notes": getattr(plan, "avoid_notes", "") or "",
        "formality_range": _range_dict(getattr(plan, "formality_range", None)),
        "warmth_range": _range_dict(getattr(plan, "warmth_range", None)),
        "temperature_celsius": temperature,
        # 用户填「0-8」这类区间时，把范围也交给模型参考（只是信息，不是规则）。
        "temperature_min_celsius": temperature_min,
        "temperature_max_celsius": temperature_max,
        # 01:56：体感偏好，只作为搭配判断的参考，不是硬规则。
        "cold_sensitivity": cold_sensitivity,
        "heat_sensitivity": heat_sensitivity,
        "weather_condition": weather_condition or "",
        # M5 Step 4：偏好按固定格式注入搭配决策的 user message。
        "user_preference": format_preference_block(preference_text),
        "pool_notes": pool.notes,
        "pool_size": len(pool.items),
        # 鞋策略：true 时每套都要配鞋，false 时一个字都不要提鞋。
        "pool_has_shoes": pool_has_shoes,
        # 用户设置的套数，prompt 会按它输出，不再按写死的 3-5 套。
        "outfit_count": outfit_count,
    }
    if required_garment_id is not None:
        context["required_garment_id"] = str(required_garment_id)
    context_text = json.dumps(context, ensure_ascii=False)
    blocks: list[dict[str, Any]] = [{"type": "text", "text": context_text}]
    if required_garment_id is not None:
        blocks.append(
            {
                "type": "text",
                "text": (
                    "EVERY outfit MUST include the garment with ID "
                    f"{required_garment_id}. Outfits without it will be discarded. "
                    "You may still return fewer than 3 outfits if that is honest."
                ),
            }
        )
    blocks += built.blocks

    last_error: Exception | None = None
    for attempt in (1,):
        try:
            started = time.monotonic()
            raw = await _call_stylist(blocks, usage)
            elapsed = time.monotonic() - started
            logger.info(
                "搭配调用完成：%.1f 秒，%d 张图，%d 个候选",
                elapsed,
                built.n_images,
                len(built.candidate_ids),
            )
            # 不再按「必须覆盖某几个槽位」校验：模型想叠穿三件上装、
            # 或者只给一条连衣裙配鞋，都应该通过。
            # 池子里确实有下装 / 一体式时，才启用「不许半身套装」兜底，
            # 避免衣橱本身没有下装时一套都返回不了。
            pool_has_lower = any(
                category_group(
                    str(getattr(item, "category", "") or "").strip().lower()
                )
                in {"bottoms", "one_piece"}
                for item in pool.items
            )
            recommendations = clean_recommendations(
                raw,
                built.candidate_ids,
                pool_has_lower=pool_has_lower,
                shoe_items=shoe_items,
            )
            if required_garment_id is not None:
                recommendations = [
                    outfit
                    for outfit in recommendations
                    if any(
                        item.garment_id == required_garment_id
                        for item in outfit.items
                    )
                ]
            # 01:30：模型给了内容但全都不成立（越界 id / 半身套装 / 0 套）
            # 是「没有合适搭配」，不是错误。以前在这里抛 ValueError，
            # 最后变成「搭配生成失败，请稍后重试」的 500，用户点了几次
            # 不喜欢之后再推荐就撞上它。现在如实返回 0 套，由前端给友好提示。
            if not recommendations:
                logger.info("本次没有可用的搭配，返回 0 套（不视为失败）")
            model_name = get_model_name(ModelRole.MULTIMODAL_STYLIST)
            return StylistResult(
                recommendations=recommendations,
                n_images=built.n_images,
                model_used=f"{model_name}|data_url|{built.n_images}",
            )
        except ModelServiceError:
            raise  # 带错误码的模型异常不能被重试吞掉
        except StylistConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - 解析/校验失败重试一次
            last_error = exc
            logger.warning("搭配决策失败（第 %d 次尝试）：%s", attempt, exc)

    raise StylistError("搭配生成失败，请稍后重试") from last_error
