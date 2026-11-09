"""Pydantic v2 schema 定义（M1 交付物：schemas.py）。

冻结范围（新版 M1.md + 用户 2026-11-05 拍板）
--------------------------------------------
- status / signal / wear_state 是真枚举（Python Enum + 数据库 CHECK 约束）。
- category 是自由文本：48 类清单在 attribute_dimensions.CATEGORY_VALUES，
  由 M2 的 vision prompt 使用；Pydantic 层不拒绝未知品类。
- garments.attributes：键名与取值必须来自 attribute_dimensions.py，
  校验只放在 Pydantic 层（AfterValidator），数据库层不加 CHECK。
- JSONB 容器（request_context / layering_plan / recommendations /
  missing_slots / vision_raw / scene_coverage 等）的具体形状由 M2/M3
  契约冻结，M1 用 Any 占位，不猜键名。
- 所有业务表外键统一叫 owner_id。

M2 兼容性：文件末尾保留了 M2 已使用的三个响应/请求契约
（GarmentCreateResult / IngestUrlRequest / ConfirmCategoryRequest）。
本轮按用户要求「M2 先不动」，不修改 M2 的业务文件。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.attribute_dimensions import ATTRIBUTE_DIMENSIONS
from app.models import EMBEDDING_DIM


class GarmentStatus(str, Enum):
    """衣物状态（M1.md 明确冻结的三值）。"""

    OWNED = "owned"
    WISHLIST = "wishlist"
    ARCHIVED = "archived"


class FeedbackSignal(str, Enum):
    """反馈信号（M1.md 明确冻结的五值）。"""

    LIKED = "liked"
    DISLIKED = "disliked"
    WORN = "worn"
    NOT_WORN = "not_worn"
    REGENERATED = "regenerated"


class WearState(str, Enum):
    """穿着状态（新版 M1.md 新增，四值）。"""

    CLEAN = "clean"
    WORN_ONCE = "worn_once"
    DIRTY = "dirty"
    LAUNDRY = "laundry"


def _check_attributes(value: dict[str, str]) -> dict[str, str]:
    """校验扩展属性：键名必须是合法维度，值必须是该维度的合法枚举。

    允许空对象 {}；缺失维度不阻止入库、也不阻止推荐（M1.md 明文要求）。
    """
    unknown: list[str] = sorted(set(value) - set(ATTRIBUTE_DIMENSIONS))
    if unknown:
        raise ValueError(
            f"未知属性维度 {unknown}；合法维度见 app/attribute_dimensions.py"
        )
    for key, item in value.items():
        allowed: list[str] = ATTRIBUTE_DIMENSIONS[key]["values"]
        if item not in allowed:
            raise ValueError(
                f"维度 {key!r} 的值 {item!r} 不合法；允许值：{allowed}"
            )
    return value


# 只在「写入」路径校验；读取路径不重复校验，避免历史数据把查询卡住。
ValidatedAttributes = Annotated[dict[str, str], AfterValidator(_check_attributes)]


class _Schema(BaseModel):
    """允许从 SQLAlchemy ORM 对象直接构造（Pydantic v2 写法）。"""

    model_config = ConfigDict(from_attributes=True)


class ColorSpec(BaseModel):
    """colors 数组中的一项：{name, hex?, role}。

    role 的示例值为 primary / secondary / accent，保持自由文本，不冻结。
    """

    name: str
    hex: str | None = None
    role: str


# ---------------------------------------------------------------- users
class UserCreate(_Schema):
    display_name: str
    settings: dict[str, Any] = Field(default_factory=dict)


class UserSchema(_Schema):
    id: uuid.UUID
    display_name: str
    created_at: datetime
    settings: dict[str, Any]


class UserNamePayload(_Schema):
    """新建 / 改名时只需要一个显示名；去掉首尾空格后不能为空。"""

    display_name: str = Field(min_length=1, max_length=40)

    @field_validator("display_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("用户名不能为空")
        return stripped


# ------------------------------------------------------------- garments
class GarmentCreate(_Schema):
    # M2：confirm-category 时沿用先前返回给用户的 garment_id；不传则由代码生成。
    # 这是可选字段，不改变 GarmentRepository.create 的接口签名。
    id: uuid.UUID | None = None
    owner_id: uuid.UUID
    status: GarmentStatus = GarmentStatus.OWNED
    category: str
    subcategory: str | None = None
    colors: list[ColorSpec] = Field(default_factory=list)
    material_guess: str | None = None
    material_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    formality: int | None = Field(default=None, ge=1, le=5)
    seasons: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    occasion_tags: list[str] = Field(default_factory=list)
    warmth: int | None = Field(default=None, ge=1, le=5)
    pattern: str | None = None
    image_url: str
    embedding: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    vision_raw: dict[str, Any] = Field(default_factory=dict)
    user_note: str | None = None
    attributes: ValidatedAttributes = Field(default_factory=dict)
    # 新版 M1.md 新增：穿着状态与最近穿着记录。
    wear_state: WearState = WearState.CLEAN
    recent_wear_dates: list[str] = Field(default_factory=list)
    last_worn_at: datetime | None = None


class GarmentSchema(_Schema):
    id: uuid.UUID
    owner_id: uuid.UUID
    status: GarmentStatus
    category: str
    subcategory: str | None
    colors: list[ColorSpec]
    material_guess: str | None
    material_confidence: float | None
    formality: int | None
    seasons: list[str]
    style_tags: list[str]
    occasion_tags: list[str]
    warmth: int | None
    pattern: str | None
    image_url: str
    embedding: list[float] | None
    vision_raw: dict[str, Any]
    user_note: str | None
    # 读取不重复校验，理由见 ValidatedAttributes 注释。
    attributes: dict[str, str]
    wear_state: WearState
    recent_wear_dates: list[str]
    last_worn_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------ outfit_sessions
class OutfitSessionCreate(_Schema):
    owner_id: uuid.UUID
    # 以下 JSON 形状由 M3 契约冻结；M1 只提供容器。
    request_context: Any = Field(default_factory=dict)
    # M3 Step 5：推荐结果一次性写入，避免「先建空会话再更新」的两段式。
    layering_plan: Any = Field(default_factory=dict)
    recommendations: Any = Field(default_factory=list)
    missing_slots: Any = Field(default_factory=list)
    token_usage: Any = Field(default_factory=dict)
    model_used: str | None = None


class OutfitSessionSchema(_Schema):
    id: uuid.UUID
    owner_id: uuid.UUID
    request_context: Any
    layering_plan: Any
    recommendations: Any
    missing_slots: Any
    token_usage: Any = Field(default_factory=dict)
    # 格式：{model_name}|{image_format}|{n_images}
    model_used: str | None
    created_at: datetime


# -------------------------------------------------------------- feedback
class FeedbackCreate(_Schema):
    session_id: uuid.UUID
    owner_id: uuid.UUID
    outfit_index: int = Field(ge=0)
    signal: FeedbackSignal
    wear_state_after: WearState | None = None
    free_text: str | None = None


class FeedbackSchema(_Schema):
    id: uuid.UUID
    session_id: uuid.UUID
    owner_id: uuid.UUID
    outfit_index: int
    signal: FeedbackSignal
    wear_state_after: WearState | None
    free_text: str | None
    created_at: datetime


class FeedbackRequest(_Schema):
    """M5：POST /feedback 的请求体（对齐 M6 前端契约）。

    - M6 只发 session_id，不发 owner_id；owner_id 由路由用 session_id 反查，
      避免前端伪造归属。
    - M6 发的是 wear_state，M1 表列名是 wear_state_after，两种写法都接受。
    """

    session_id: uuid.UUID
    outfit_index: int = Field(ge=0)
    signal: FeedbackSignal
    wear_state: WearState | None = Field(
        default=None,
        validation_alias=AliasChoices("wear_state", "wear_state_after"),
    )
    free_text: str | None = None


# ------------------------------------------------------ user_preferences
class PreferenceUpsert(_Schema):
    owner_id: uuid.UUID
    preference_text: str


class UserPreferenceSchema(_Schema):
    owner_id: uuid.UUID
    preference_text: str
    version: int
    updated_at: datetime


class PreferenceHistorySchema(_Schema):
    """偏好历史的一行（user_preferences_history 表）。"""

    id: uuid.UUID
    owner_id: uuid.UUID
    preference_text: str
    version: int
    created_at: datetime


# -------------------------------------------------- wishlist_evaluations
class WishlistEvaluationCreate(_Schema):
    owner_id: uuid.UUID
    candidate_garment_id: uuid.UUID
    existing_similar_ids: list[str] = Field(default_factory=list)
    feasible_outfit_count: int = Field(default=0, ge=0)
    scene_coverage: list[str] = Field(default_factory=list)
    llm_verdict: str
    llm_reasoning: str


class WishlistEvaluationSchema(_Schema):
    id: uuid.UUID
    owner_id: uuid.UUID
    candidate_garment_id: uuid.UUID
    existing_similar_ids: list[str]
    feasible_outfit_count: int
    scene_coverage: list[str]
    llm_verdict: str
    llm_reasoning: str
    created_at: datetime


# ------------------------------------------------------- M2：入库流水线契约
class GarmentCreateResult(_Schema):
    """POST /ingest/image 与 /ingest/url 的响应（M2.md「API 契约」）。

    注意：status=needs_category_confirmation 时衣物尚未写入数据库，
    garment_id 是待确认记录的主键；用户 confirm 后才以同一 id 真正入库。
    """

    garment_id: uuid.UUID
    status: Literal["auto_accepted", "needs_category_confirmation"]
    extracted: dict[str, Any]
    confirmation_options: list[str] | None = None


class IngestUrlRequest(_Schema):
    url: str
    # M1 没有认证模块；owner_id 可选：缺省时读 AIDROBE_DEFAULT_OWNER_ID，
    # 两者都没有则路由返回 422（不猜一个用户）。
    owner_id: uuid.UUID | None = None
    # purchase = 购前候选，只建 wishlist 记录，不进衣橱与推荐。
    purpose: str | None = None


class ConfirmCategoryRequest(_Schema):
    garment_id: uuid.UUID
    category: str


# ------------------------------------------------------- M3：推荐接口契约
class LocationSpec(_Schema):
    """M3.md：location 可以是 {"lat","lon"} 或 {"city"}。"""

    lat: float | None = None
    lon: float | None = None
    city: str | None = None


class RecommendRequest(_Schema):
    # M3.md 写 user_id；M1 统一 owner_id，这里两个都接受（owner_id 优先）。
    owner_id: uuid.UUID = Field(
        validation_alias=AliasChoices("owner_id", "user_id")
    )
    scene: Literal[
        "work", "date", "weekend", "travel", "formal-event", "free_text"
    ]
    free_text: str | None = None
    location: LocationSpec | None = None
    temperature_override: int | None = None
    # 01:30：温度覆盖允许「0-8」这样的区间，由前端解析后分别传入。
    temperature_override_min: float | None = None
    temperature_override_max: float | None = None
    # shared 目前退化为 all（M1 没有家庭关系表），行为见 routers/recommend.py
    wardrobe_scope: Literal["mine", "shared", "all"] = "mine"


class WeatherInfo(_Schema):
    temperature: float | None = None
    temperature_max: float | None = None
    temperature_min: float | None = None
    city: str | None = None
    condition: str | None = None


class RecommendResponse(_Schema):
    session_id: uuid.UUID
    weather: WeatherInfo
    # U1：候选池的中文说明（例如「按正式度筛选后可用太少，已放宽条件」）。
    # 旧字段名 missing_slots 属于已废除的槽位体系，接口不再对外暴露。
    pool_notes: list[str] = Field(default_factory=list)
    layering_plan: dict[str, Any]
    recommendations: list[dict[str, Any]]


# ------------------------------------------------------- M4：购前决策接口契约
# 请求字段兼容说明（2026-11-06）：
#   M4.md 写 {user_id, candidate_garment_id}，M6 前端 api/client.ts 实际发
#   {owner_id, garment_id}。用 AliasChoices 四种写法都收，内部一律 owner_id。
#   这是「接口兼容」，不改变 M1 冻结的数据库 schema。
class EvaluatePurchaseRequest(_Schema):
    owner_id: uuid.UUID = Field(
        validation_alias=AliasChoices("owner_id", "user_id")
    )
    candidate_garment_id: uuid.UUID = Field(
        validation_alias=AliasChoices("candidate_garment_id", "garment_id")
    )
    # 可选覆盖 users.settings.strictness；不传则读用户设置，默认 conservative。
    strictness: Literal["conservative", "permissive"] | None = None


class GapReportRequest(_Schema):
    owner_id: uuid.UUID = Field(
        validation_alias=AliasChoices("owner_id", "user_id")
    )
    # shared 目前退化为 all（M1 没有家庭关系表），与 M3 行为一致。
    wardrobe_scope: Literal["mine", "shared", "all"] = "mine"


class SimilarGarmentItem(_Schema):
    """M6 BuyTab 会展示的相似单品（含图片 URL）。"""

    garment_id: uuid.UUID
    image_url: str | None = None
    category: str | None = None
    subcategory: str | None = None
    similarity: float | None = None
    similarity_level: str | None = None
    key_differences: str | None = None


class PurchaseEvaluationResponse(_Schema):
    """POST /evaluate-purchase 的响应。

    M1 的 wishlist_evaluations 表没有 duplicate_check / duplicate_risk 两列，
    也不为本模块改表或新增迁移，所以这两个字段只出现在响应里。
    verdict/reasoning 与 llm_verdict/llm_reasoning 是同一份内容的两种写法：
    前者给 M6 前端读，后者与 M1 表列名保持一致。
    """

    id: uuid.UUID | None = None
    owner_id: uuid.UUID
    candidate_garment_id: uuid.UUID
    verdict: str
    llm_verdict: str
    reasoning: str
    llm_reasoning: str
    duplicate_check: Literal["ok", "no_match", "unavailable"]
    duplicate_risk: Literal["high", "medium", "low", "none"]
    existing_similar_ids: list[str]
    similar_items: list[SimilarGarmentItem]
    feasible_outfit_count: int
    scene_coverage: list[str]
    created_at: datetime | None = None


class GapReportResponse(_Schema):
    owner_id: uuid.UUID
    wardrobe_scope: str
    statistics: dict[str, Any]
    gaps: list[str]
    summary: str
