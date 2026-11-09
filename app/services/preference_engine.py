"""M5 偏好引擎：反馈 → 偏好文本 → 注入。

职责边界
--------
- 只做「偏好文本的抽取、存储与注入」，不训练模型、不做向量。
- 模型调用统一走 `TEXT_PLANNER`（temperature=0.3），不在本文件硬编码模型名。
- 数据访问全部经 `PreferenceEngineRepository`，本层不写 SQL。
- 不单独开进程、不抢锁：由路由的 BackgroundTasks 在反馈写完后调用，
  复用 db_gateway 的唯一句柄（M7 挂载后即 `app.state.db`）。

触发策略（M5.md）
----------------
- 每积累 5 条新反馈，或距上次重写满 7 天（先到者）。
- 用户主动说「记住我喜欢……」时立即触发。
- 反馈总数 < 5 且非主动触发时，preference_text 保持为空。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.llm_config import (
    model_extra_kwargs,
    JSON_MODE_HINT,
    ModelConfigError,
    ModelRole,
    get_client,
    get_model_name,
)
from app.prompts import load_prompt
from app.repositories.base import PreferenceEngineRepository
from app.schemas import PreferenceUpsert, UserPreferenceSchema

logger: logging.Logger = logging.getLogger(__name__)

FEEDBACK_WINDOW: int = 30
REWRITE_EVERY_N: int = 5
REWRITE_EVERY_DAYS: int = 7
MAX_PREFERENCE_CHARS: int = 120
REWRITE_TEMPERATURE: float = 0.3

# 用户主动要求记住偏好时的触发短语（M5.md「触发策略」第二条）。
EXPLICIT_TRIGGER_PHRASES: tuple[str, ...] = (
    "记住我喜欢",
    "记住我的偏好",
    "记住我偏好",
    "记住：我喜欢",
    "记住,我喜欢",
    "记得我喜欢",
)

# 极简歧视性表述过滤表。M5.md 只要求「过滤该条反馈」，未给词表；
# 这里保持最小集合，后续可外置到配置，避免把正常审美偏好误杀。
DISCRIMINATORY_TERMS: tuple[str, ...] = (
    "低等人",
    "劣等",
    "娘娘腔",
    "男人婆",
    "乡巴佬",
    "土包子",
    "肥猪",
    "丑八怪",
)

_UUID_RE: re.Pattern[str] = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class PreferenceModelUnavailable(RuntimeError):
    """TEXT_PLANNER 的环境变量没配好；重写只能跳过。"""


class PreferenceRewriteError(RuntimeError):
    """模型输出不可用（非 JSON / 空文本 / 字段缺失）。"""


@dataclass
class RewriteDecision:
    should_rewrite: bool
    reason: str
    explicit: bool = False


@dataclass
class RewriteOutcome:
    rewritten: bool
    reason: str
    preference_text: str | None = None
    version: int | None = None


def _as_aware(value: datetime | None) -> datetime | None:
    """把可能来自数据库的 naive 时间统一成 UTC aware，便于相减。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_explicit_trigger(free_text: str | None) -> bool:
    """用户是否在反馈里主动要求「记住我喜欢……」。"""
    if not free_text:
        return False
    return any(phrase in free_text for phrase in EXPLICIT_TRIGGER_PHRASES)


def _is_discriminatory(text: str | None) -> bool:
    if not text:
        return False
    return any(term in text for term in DISCRIMINATORY_TERMS)


def sanitize_preference(text: str) -> str:
    """清洗模型输出：去 garment_id/UUID、压空白、截断到 120 字。"""
    cleaned: str = _UUID_RE.sub("", text).replace("garment_id", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > MAX_PREFERENCE_CHARS:
        logger.info(
            "偏好文本 %d 字，超过上限 %d，已截断",
            len(cleaned),
            MAX_PREFERENCE_CHARS,
        )
        cleaned = cleaned[:MAX_PREFERENCE_CHARS].rstrip()
    return cleaned


async def decide_rewrite(
    repo: PreferenceEngineRepository,
    owner_id: uuid.UUID,
    *,
    explicit: bool = False,
    now: datetime | None = None,
) -> RewriteDecision:
    """判断这次反馈写完后要不要重写偏好。"""
    current_time: datetime = _as_aware(now) or datetime.now(timezone.utc)
    total: int = await repo.count_feedback(owner_id)

    if explicit:
        if total == 0:
            return RewriteDecision(False, "用户主动要求，但还没有任何反馈", True)
        return RewriteDecision(True, "用户主动要求记住偏好", True)

    if total < REWRITE_EVERY_N:
        return RewriteDecision(
            False, f"反馈仅 {total} 条，未达到 {REWRITE_EVERY_N} 条下限"
        )

    state: UserPreferenceSchema | None = await repo.get_preference_state(owner_id)
    if state is None:
        return RewriteDecision(True, f"已有 {total} 条反馈，首次生成偏好")

    new_count: int = await repo.count_feedback_since(
        owner_id, _as_aware(state.updated_at)
    )
    if new_count >= REWRITE_EVERY_N:
        return RewriteDecision(
            True, f"较上次重写新增 {new_count} 条反馈（≥{REWRITE_EVERY_N}）"
        )

    last: datetime | None = _as_aware(state.updated_at)
    if last is not None and current_time - last >= timedelta(days=REWRITE_EVERY_DAYS):
        return RewriteDecision(
            True, f"距上次重写已满 {REWRITE_EVERY_DAYS} 天"
        )
    return RewriteDecision(
        False, f"新增 {new_count} 条反馈，且距上次重写不足 {REWRITE_EVERY_DAYS} 天"
    )


async def build_context(
    repo: PreferenceEngineRepository,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把反馈行整理成给模型看的紧凑上下文（不含 garment_id / 品牌名）。"""
    garment_ids: set[uuid.UUID] = set()
    for row in rows:
        session = row.get("session") or {}
        recommendations = session.get("recommendations") or []
        index = row.get("outfit_index")
        if (
            isinstance(recommendations, list)
            and isinstance(index, int)
            and 0 <= index < len(recommendations)
        ):
            for item in recommendations[index].get("items") or []:
                raw_id = item.get("garment_id")
                try:
                    garment_ids.add(uuid.UUID(str(raw_id)))
                except (ValueError, TypeError):
                    continue

    profiles = await repo.get_garment_profiles(garment_ids)
    context: list[dict[str, Any]] = []
    for row in rows:
        feedback_text = row.get("free_text")
        if _is_discriminatory(feedback_text):
            logger.info("跳过含歧视性表述的反馈：%r", feedback_text[:40])
            continue

        session = row.get("session") or {}
        request_context = session.get("request_context") or {}
        recommendations = session.get("recommendations") or []
        index = row.get("outfit_index")
        outfit: dict[str, Any] = {}
        if (
            isinstance(recommendations, list)
            and isinstance(index, int)
            and 0 <= index < len(recommendations)
            and isinstance(recommendations[index], dict)
        ):
            outfit = recommendations[index]

        items: list[dict[str, Any]] = []
        for item in outfit.get("items") or []:
            if not isinstance(item, dict):
                continue
            profile: dict[str, Any] = {}
            raw_id = item.get("garment_id")
            if raw_id is not None:
                try:
                    profile = profiles.get(uuid.UUID(str(raw_id)), {}) or {}
                except (ValueError, TypeError):
                    profile = {}
            items.append(
                {
                    "category": item.get("category") or profile.get("category"),
                    "colors": profile.get("colors"),
                    "formality": profile.get("formality"),
                    "style_tags": profile.get("style_tags"),
                }
            )

        context.append(
            {
                "signal": row.get("signal"),
                "wear_state_after": row.get("wear_state_after"),
                "style_direction": outfit.get("style_direction"),
                "items": items,
                "scene": request_context.get("scene"),
                "temperature": request_context.get("temperature_override"),
                "free_text": request_context.get("free_text"),
                "feedback_free_text": feedback_text,
            }
        )
    return context


def _extract_text(choice: Any) -> str:
    content = choice.message.content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return str(content or "")


def _parse_json(content: str) -> dict[str, Any]:
    text: str = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise PreferenceRewriteError("偏好重写结果不是 JSON 对象")
    return data


async def call_rewriter(
    current_text: str, context: list[dict[str, Any]]
) -> str:
    """调用 TEXT_PLANNER（temperature=0.3）重写偏好，返回清洗后的文本。"""
    try:
        client = get_client(ModelRole.TEXT_PLANNER)
        model = get_model_name(ModelRole.TEXT_PLANNER)
    except ModelConfigError as exc:
        raise PreferenceModelUnavailable(str(exc)) from exc

    payload: dict[str, Any] = {
        "current_preference": current_text or "",
        "recent_feedback": context,
    }
    completion = await client.chat.completions.create(
        model=model,
        **model_extra_kwargs(model),
        messages=[
            {"role": "system", "content": load_prompt("preference_rewrite") + JSON_MODE_HINT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        temperature=REWRITE_TEMPERATURE,
        response_format={"type": "json_object"},
    )
    raw: dict[str, Any] = _parse_json(_extract_text(completion.choices[0]))
    value = raw.get("preference_text")
    if not isinstance(value, str) or not value.strip():
        raise PreferenceRewriteError("模型返回的 preference_text 为空")
    return sanitize_preference(value)


async def rewrite_preference(
    repo: PreferenceEngineRepository,
    owner_id: uuid.UUID,
    *,
    explicit: bool = False,
    now: datetime | None = None,
) -> RewriteOutcome:
    """完整流程：判断触发 → 拉上下文 → 重写 → 写历史 + upsert。"""
    decision: RewriteDecision = await decide_rewrite(
        repo, owner_id, explicit=explicit, now=now
    )
    if not decision.should_rewrite:
        logger.info("偏好不重写：%s", decision.reason)
        return RewriteOutcome(False, decision.reason)

    rows: list[dict[str, Any]] = await repo.list_feedback_context(
        owner_id, limit=FEEDBACK_WINDOW
    )
    if not rows:
        return RewriteOutcome(False, "没有可用于重写的反馈上下文")

    state: UserPreferenceSchema | None = await repo.get_preference_state(owner_id)
    current_text: str = state.preference_text if state else ""
    current_version: int = state.version if state else 0
    context: list[dict[str, Any]] = await build_context(repo, rows)

    try:
        new_text: str = await call_rewriter(current_text, context)
    except (PreferenceModelUnavailable, PreferenceRewriteError, ValueError) as exc:
        logger.warning("偏好重写失败，保留旧文本：%s", exc)
        return RewriteOutcome(False, f"重写失败：{exc}")

    # Step 3：旧文本先入历史（含旧 version），再 upsert 新文本（version+1）。
    if current_text.strip():
        await repo.push_history(owner_id, current_text, current_version)
    updated: UserPreferenceSchema = await repo.upsert_preference_text(
        PreferenceUpsert(owner_id=owner_id, preference_text=new_text)
    )
    logger.info(
        "偏好已更新：owner=%s version=%d chars=%d",
        owner_id,
        updated.version,
        len(updated.preference_text),
    )
    return RewriteOutcome(
        True, decision.reason, updated.preference_text, updated.version
    )


async def clear_preference(
    repo: PreferenceEngineRepository, owner_id: uuid.UUID
) -> UserPreferenceSchema:
    """用户清空偏好：旧文本入历史，当前快照写空，version 继续递增。"""
    state: UserPreferenceSchema | None = await repo.get_preference_state(owner_id)
    if state is not None and state.preference_text.strip():
        await repo.push_history(owner_id, state.preference_text, state.version)
    return await repo.upsert_preference_text(
        PreferenceUpsert(owner_id=owner_id, preference_text="")
    )


async def run_rewrite_in_background(
    owner_id: uuid.UUID,
    *,
    explicit: bool = False,
) -> None:
    """BackgroundTasks 入口：复用 db_gateway 唯一句柄，自建会话。

    请求侧的会话在 `async with` 退出时已关闭，这里再开一个新会话，
    保证后台任务不会与 Web 请求并发写同一条连接（M5 验收 7）。

    U4 裁决 3：Key 统一从后端 .env 读，后台任务不再需要携带请求凭据。
    """
    # 延迟导入，避免 db_gateway <-> services 的循环依赖。
    from app.db import ensure_database, get_session_factory
    from app.repositories.postgres import PostgresPreferenceEngineRepository

    try:
        await ensure_database()
        factory = get_session_factory()
        async with factory() as session:
            repo = PostgresPreferenceEngineRepository(session)
            await rewrite_preference(repo, owner_id, explicit=explicit)
    except Exception as exc:  # noqa: BLE001 - 后台任务失败不能影响已返回的响应
        logger.warning("后台偏好重写失败（已忽略）：%s", exc)
