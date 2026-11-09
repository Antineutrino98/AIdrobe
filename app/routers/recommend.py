"""M3 穿搭推荐接口（POST /recommend）。

三步职责分离（M3.md）：
1. TEXT_PLANNER 生成检索计划（services/retrieval_planner.py）
2. 代码按计划检索并压缩候选（services/filter.py）
3. MULTIMODAL_STYLIST 看候选图片做搭配（services/stylist_llm.py）

本文件只做：参数校验、天气获取、编排上面三步、把结果写入 outfit_sessions。
不在路由里写 SQL、不直接拼 LLM 请求。

wardrobe_scope 说明：M1 没有「家庭/伴侣关系」表，`shared` 无法判定成员，
暂时退化为 `all` 并记日志（不做假实现）；`mine` 严格按 owner_id。
"""
from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import ensure_database, get_session_factory
from app.repositories.postgres import (
    PostgresGarmentRepository,
    PostgresPreferenceRepository,
    PostgresSessionRepository,
)
from app.schemas import (
    LocationSpec,
    OutfitSessionCreate,
    RecommendRequest,
    RecommendResponse,
    WeatherInfo,
)
from app.services.filter import (
    POOL_LIMIT,
    build_global_pool,
    is_recommendable,
)
from app.services.recommend_policy import (
    RecommendPolicy,
    apply_policy,
    dress_code_hint,
    parse_policy,
)
from app.services.retrieval_planner import (
    PlannerConfigError,
    PlannerError,
    plan_retrieval,
)
from app.services.usage import UsageCollector
from app.services.stylist_llm import (
    StylistConfigError,
    StylistError,
    rank_outfits,
)

logger: logging.Logger = logging.getLogger(__name__)

WEATHER_TIMEOUT_SECONDS: float = 10.0
GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_WEATHER_API_BASE: str = "https://api.open-meteo.com/v1"
WARDROBE_LIMIT: int = 1000
MIN_RECOMMENDATIONS: int = 3

router: APIRouter = APIRouter(tags=["recommend"])


def forecast_url() -> str:
    """M7：天气 API 基址可在 .env 里改，调用时读取，避免导入期固定。"""
    base: str = os.environ.get(
        "WEATHER_API_BASE", DEFAULT_WEATHER_API_BASE
    ).rstrip("/")
    return f"{base}/forecast"


def _geocode_score(item: dict[str, Any], query: str) -> float:
    """给地理编码结果打分，避免「吉林」命中南方某个同名里 / 村。

    纯地理消歧：优先省级行政区同名、行政中心、人口大的地点；
    里 / 村级同名地点降权。不涉及任何行政区划归属判断。
    """
    name = str(item.get("name") or "")
    admin1 = str(item.get("admin1") or "")
    admin2 = str(item.get("admin2") or "")
    admin4 = str(item.get("admin4") or "")
    core = query.rstrip("市省县区")
    score = 0.0
    if core and core in admin1:
        score += 100  # 「吉林」→ 吉林省
    if core and core in admin2:
        score += 40
    if admin4:
        score -= 60  # 里 / 村级同名地点
    if name == query or name == core:
        score += 20
    feature = str(item.get("feature_code") or "")
    if feature == "PPLC" or feature.startswith("PPLA"):
        score += 30
    population = item.get("population") or 0
    try:
        score += min(float(population), 2_000_000) / 100_000
    except (TypeError, ValueError):
        pass
    return score


def weather_text(code: int | None) -> str | None:
    """WMO weather code → 简短英文描述（M3 响应里的 condition）。"""
    if code is None:
        return None
    if code == 0:
        return "clear"
    if code in (1, 2, 3):
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 82:
        return "showers"
    if 95 <= code <= 99:
        return "thunderstorm"
    return "unknown"


async def fetch_weather(
    location: LocationSpec | None,
    temperature_override: int | None,
    temperature_min: float | None = None,
    temperature_max: float | None = None,
) -> WeatherInfo:
    """Open-Meteo 取当前温度；任何失败都降级为未知，不影响推荐。

    01:30：用户会在「温度覆盖」里填「0-8」这样的区间，前端解析成 min/max
    传过来。以前只接受单个整数，Number("0-8") 变 NaN → null → 温度未知。
    """
    if temperature_min is not None or temperature_max is not None:
        low = temperature_min if temperature_min is not None else temperature_max
        high = temperature_max if temperature_max is not None else temperature_min
        assert low is not None and high is not None
        low, high = min(low, high), max(low, high)
        return WeatherInfo(
            temperature=(low + high) / 2,
            temperature_min=low,
            temperature_max=high,
            city=location.city if location else None,
        )
    if temperature_override is not None:
        return WeatherInfo(
            temperature=float(temperature_override),
            city=location.city if location else None,
        )
    if location is None:
        return WeatherInfo()

    latitude, longitude = location.lat, location.lon
    city_name: str | None = location.city or None
    try:
        async with httpx.AsyncClient(timeout=WEATHER_TIMEOUT_SECONDS) as client:
            if latitude is None or longitude is None:
                if not location.city:
                    return WeatherInfo()
                # 00:51 实测：查「吉林」时 Open-Meteo 的第一条是某地里级同名
                # 地点（北纬 25 度），导致十一月拿到 16-20℃。取多条结果，
                # 按省级行政区同名、行政中心级别、人口规模消歧，别只取第一条。
                geo = await client.get(
                    GEOCODING_URL,
                    params={"name": location.city, "count": 10, "language": "zh"},
                )
                geo.raise_for_status()
                results = (geo.json() or {}).get("results") or []
                if not results:
                    logger.warning("城市 %r 没有地理编码结果，天气未知", location.city)
                    return WeatherInfo(city=city_name)
                query = (location.city or "").strip()
                best = max(results, key=lambda item: _geocode_score(item, query))
                latitude = best.get("latitude")
                longitude = best.get("longitude")
                city_name = str(best.get("name") or location.city)
                logger.info(
                    "天气地理编码：%r → %s（%s / %s，%.4f, %.4f）",
                    query,
                    city_name,
                    best.get("admin1") or "",
                    best.get("country") or "",
                    float(latitude or 0),
                    float(longitude or 0),
                )

            response = await client.get(
                forecast_url(),
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "forecast_days": 1,
                    "timezone": "auto",
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json() or {}
            current = payload.get("current") or {}
            daily = payload.get("daily") or {}
            t_max = (daily.get("temperature_2m_max") or [None])[0]
            t_min = (daily.get("temperature_2m_min") or [None])[0]
            return WeatherInfo(
                temperature=current.get("temperature_2m"),
                temperature_max=t_max,
                temperature_min=t_min,
                city=city_name,
                condition=weather_text(current.get("weather_code")),
            )
    except Exception as exc:  # noqa: BLE001 - 天气失败不能阻断推荐
        logger.warning("天气获取失败，降级为未知：%s", exc)
        return WeatherInfo()


def category_counts(garments: list[Any]) -> dict[str, int]:
    """给 planner 的衣橱摘要：只统计 owned 且非 dirty/laundry。"""
    counts: dict[str, int] = {}
    for garment in garments:
        if not is_recommendable(garment):
            continue
        counts[garment.category] = counts.get(garment.category, 0) + 1
    return counts


class RecommendService:
    """编排：天气 → 计划 → 检索 → 搭配 → 写会话。"""

    def __init__(
        self,
        garments: PostgresGarmentRepository,
        sessions: PostgresSessionRepository,
        preferences: PostgresPreferenceRepository,
    ) -> None:
        self._garments = garments
        self._sessions = sessions
        self._preferences = preferences

    async def _load_wardrobe(
        self, owner_id: uuid.UUID, scope: str
    ) -> list[Any]:
        if scope == "mine":
            return await self._garments.list_by_owner(owner_id, limit=WARDROBE_LIMIT)
        if scope == "shared":
            logger.warning(
                "wardrobe_scope=shared 需要家庭关系表（M1 未提供），已退化为 all"
            )
        return await self._garments.list_all(limit=WARDROBE_LIMIT)

    async def recommend(
        self,
        request: RecommendRequest,
        policy: RecommendPolicy | None = None,
    ) -> RecommendResponse:
        # 策略从 users.settings 读取；测试用的假 repository 没有该方法时
        # 用默认策略，保持 M3 的既有行为不变。
        if policy is None:
            loader = getattr(self._preferences, "get_user_settings", None)
            settings = (
                await loader(request.owner_id) if loader is not None else {}
            )
            policy = parse_policy(settings)
        weather: WeatherInfo = await fetch_weather(
            request.location,
            request.temperature_override,
            request.temperature_override_min,
            request.temperature_override_max,
        )
        preference: str | None = await self._preferences.get_preference_text(
            request.owner_id
        )
        garments = await self._load_wardrobe(
            request.owner_id, request.wardrobe_scope
        )
        # 数据都在内存里了，立刻把唯一的数据库连接还回池子：
        # 下面要串行调用 4 次模型（可能 40 秒），占着连接会让整个应用假死。
        release = getattr(self._garments, "release", None)
        if release is not None:
            await release()
        garments = apply_policy(garments, policy)
        min_outfits: int = policy.outfit_count
        counts = category_counts(garments)

        # U4：一次推荐里两次模型调用的用量，最后统一写进会话。
        usage = UsageCollector()
        plan = await plan_retrieval(
            scene=request.scene,
            free_text=request.free_text,
            temperature=weather.temperature,
            temperature_min=weather.temperature_min,
            temperature_max=weather.temperature_max,
            weather_condition=weather.condition,
            preference_text=preference,
            category_counts=counts,
            dress_code_hint=dress_code_hint(policy.dress_code, request.scene),
            cold_sensitivity=policy.cold_sensitivity,
            heat_sensitivity=policy.heat_sensitivity,
            usage=usage,
        )

        # U1：一个混合候选池，不再按槽位分桶。
        pool = build_global_pool(
            garments,
            plan,
            limit=POOL_LIMIT,
            pool_fallback=policy.pool_fallback,
        )
        # 严格模式下池子可能太空：如实返回 0 套，不硬凑，也不白等一次模型调用。
        recommendations: list[dict[str, Any]] = []
        model_used = "pool-empty"
        if len(pool.items) >= 2:
            # 只调用一次模型。之前「不足 3 套就放宽候选再调一次」的循环，
            # 单次调用约 80 秒，最坏会串成 4 次、让用户干等十分钟。
            result = await rank_outfits(
                pool=pool,
                scene=request.scene,
                temperature=weather.temperature,
                weather_condition=weather.condition,
                temperature_min=weather.temperature_min,
                temperature_max=weather.temperature_max,
                cold_sensitivity=policy.cold_sensitivity,
                heat_sensitivity=policy.heat_sensitivity,
                preference_text=preference,
                plan=plan,
                usage=usage,
                # 用户设置的「每次至少生成几套」必须真的告诉模型，
                # 否则它只会按 prompt 里的默认值给，设置形同虚设。
                outfit_count=min_outfits,
            )
            if len(result.recommendations) < min_outfits:
                logger.info(
                    "本次只给出 %d 套（期望 %d 套），直接返回，不再重试",
                    len(result.recommendations),
                    min_outfits,
                )
            recommendations = [
                item.model_dump(mode="json") for item in result.recommendations
            ]
            model_used = result.model_used
        else:
            pool.notes.append(
                "按当前温度、场合与正式度要求，衣橱里没有足够合适的单品；"
                "可在「推荐策略」里改成「勉强推荐」，或先补充衣物。"
            )
            logger.info("严格模式下候选池仅 %d 件，返回 0 套", len(pool.items))
        layering_plan: dict[str, Any] = plan.model_dump(mode="json")

        session = await self._sessions.create_session(
            OutfitSessionCreate(
                owner_id=request.owner_id,
                request_context=request.model_dump(mode="json"),
                layering_plan=layering_plan,
                recommendations=recommendations,
                missing_slots=pool.notes,
                token_usage=usage.to_json(),
                model_used=model_used,
            )
        )
        logger.info(
            "推荐完成：session=%s，套数=%d，候选池 %d 件，说明 %d 条",
            session.id,
            len(recommendations),
            len(pool.items),
            len(pool.notes),
        )
        return RecommendResponse(
            session_id=session.id,
            weather=weather,
            pool_notes=list(pool.notes),
            layering_plan=layering_plan,
            recommendations=recommendations,
        )


async def get_recommend_service() -> AsyncIterator[RecommendService]:
    """每个请求一个 AsyncSession；数据库由 db_gateway 唯一句柄提供。"""
    await ensure_database()
    factory = get_session_factory()
    async with factory() as session:
        yield RecommendService(
            PostgresGarmentRepository(session),
            PostgresSessionRepository(session),
            PostgresPreferenceRepository(session),
        )


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(
    body: RecommendRequest,
    service: RecommendService = Depends(get_recommend_service),
) -> RecommendResponse:
    try:
        return await service.recommend(body)
    except (PlannerConfigError, StylistConfigError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (PlannerError, StylistError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
