"""M7：AIdrobe 应用入口。

启动顺序（与 M7.md 一致）
------------------------
1. 读取配置（`.env` → 环境变量）；
2. `llm_config.validate_all_roles()`：三个模型角色任一缺失 → 启动失败；
3. `db_gateway.open_database()`：flock 加锁 → 启动前备份 → WAL 检测打开；
4. 注入 `app.state.db`（`lock_fd` 由 DatabaseHandle 内部持有，这里留占位）；
5. `alembic upgrade head`：**复用已打开的唯一连接**，不再开第二条连接；
6. 确保图片目录存在；
7. 挂载 M2–M5 的全部 router。

关闭
----
uvicorn 收到 SIGTERM/SIGINT 后会走 lifespan 的 `finally`：
`close_database()` 释放引擎 → 关闭 PGlite 子进程 → 释放 flock。
flock 由内核释放，本模块不删除锁文件。

为什么迁移要复用连接
--------------------
PGlite 的 socket 服务只支持一个活动连接。若在 lifespan 里用
`alembic` CLI 子进程迁移，会开第二条连接并抢同一个数据目录，必然失败。
因此 `alembic/env.py` 增加了「注入连接」分支，这里通过
`engine.begin()` + `run_sync` 把同步 Connection 交给 Alembic。
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Connection
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

from app.config import BASE_DIR, Settings
from app.db_gateway import close_database, open_database
from app.errors import ModelServiceError
from app.llm_config import validate_all_roles
from app.prompts import set_ui_language
from app.routers import (
    feedback,
    garments,
    ingest,
    purchase,
    recommend,
    settings,
    system,
    users,
)

logger: logging.Logger = logging.getLogger(__name__)


def _resolve_path(value: str) -> str:
    """相对路径按项目根解析，避免从别的 cwd 启动时数据写错位置。"""
    path = Path(value)
    return str(path if path.is_absolute() else BASE_DIR / path)


def _migrate(connection: Connection) -> None:
    """在已有的同步连接上执行 Alembic 迁移（不新开连接）。"""
    config = AlembicConfig(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "alembic"))
    config.attributes["connection"] = connection
    # 别让 Alembic 的 fileConfig 关掉 uvicorn 的 logger。
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = Settings.from_env()
    settings.apply_env_compat()

    # 图片目录统一成绝对路径，M2 的 image_dir() 直接读环境变量。
    image_dir = _resolve_path(settings.image_dir)
    os.environ["IMAGE_STORAGE_DIR"] = image_dir
    os.environ["IMAGE_DIR"] = image_dir

    # 1. base_url / model 必须能解析；Key 可留空（前端「模型设置」输入）。
    validate_all_roles(require_keys=False)

    # 2. 打开唯一数据库句柄（加锁 → 备份 → WAL 检测）。
    db = await open_database(
        _resolve_path(settings.pglite_data_dir),
        _resolve_path(settings.pglite_backup_dir),
    )
    app.state.db = db
    # M7 契约占位：锁实际由 DatabaseHandle 内部持有，关闭时自动释放。
    app.state.lock_fd = None

    try:
        # 3. 迁移复用同一连接；PGlite 单连接，绝不在这里开第二条。
        async with db.engine.begin() as connection:
            await connection.run_sync(_migrate)

        # 4. 图片目录（M2 的图片路由从这里读）。
        Path(image_dir).mkdir(parents=True, exist_ok=True)
        logger.info(
            "AIdrobe 就绪：data_dir=%s backend=%s",
            settings.pglite_data_dir,
            db.backend,
        )
        yield
    finally:
        logger.info("AIdrobe 正在关闭数据库…")
        await close_database(app.state.db)
        app.state.db = None


app: FastAPI = FastAPI(
    title="AIdrobe",
    version="1.0.0",
    description="智能衣橱：入库、推荐、购前决策与偏好学习",
    lifespan=lifespan,
)


@app.exception_handler(ModelServiceError)
async def model_service_error_handler(
    request: Request, exc: ModelServiceError
) -> JSONResponse:
    """U4 模块三：把模型异常转成带稳定 code 的 JSON，供前端友好提示。"""
    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "message": str(exc)},
    )


# M2–M5 的全部路由 + M7 的用户/单品只读接口 + 自检接口。
app.include_router(ingest.router)
app.include_router(ingest.images_router)
app.include_router(recommend.router)
app.include_router(purchase.router)
app.include_router(feedback.router)
app.include_router(users.router)
app.include_router(garments.router)
app.include_router(system.router)
app.include_router(settings.router)


@app.middleware("http")
async def strip_api_prefix_middleware(request: Request, call_next: object):
    """把 `/api/xxx` 改写为 `/xxx`。

    前端构建产物里所有请求都带 `/api` 前缀（开发时由 Vite 代理剥掉）。
    现在改由后端直接托管前端页面，没有 Vite 了，所以在这里剥一次。
    这样**不需要重新构建前端**，同一份 dist 在开发和生产都能用。
    """
    # 21:56 反馈：英文界面时让模型也用英文写理由 / 结论，避免中英夹杂。
    # 中文界面下 language_instruction() 返回空串，prompt 一字不改。
    set_ui_language(request.headers.get("x-ui-language"))
    path: str = request.scope.get("path", "")
    # 留一份原始路径，供静态托管的 SPA 回退判断「这是不是接口请求」：
    # /api/xxx 被剥成 /xxx 之后，若不保留原值，接口 404 会被误当成前端路由。
    request.scope["aidrobe_original_path"] = path
    if path == "/api" or path.startswith("/api/"):
        new_path: str = path[4:] or "/"
        request.scope["path"] = new_path
        raw = request.scope.get("raw_path")
        if isinstance(raw, bytes):
            request.scope["raw_path"] = new_path.encode("utf-8")
    return await call_next(request)  # type: ignore[operator]


# 看起来像静态资源文件的后缀。这些文件缺失时必须老实报 404，
# 不能回退成 HTML —— 否则浏览器会把 index.html 当成 JS/CSS 解析。
_ASSET_SUFFIXES: frozenset[str] = frozenset(
    {
        ".js", ".mjs", ".cjs", ".css", ".map", ".json", ".ico",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
        ".woff", ".woff2", ".ttf", ".eot", ".otf", ".wasm",
        ".txt", ".xml", ".pdf",
    }
)


class _SPAStaticFiles(StaticFiles):
    """单页应用静态托管：未知的前端路由回退到 index.html。

    前端加上客户端路由后，用户直接在 `/settings`、`/wear/today` 这类地址
    刷新时，服务器上并没有同名文件，普通 StaticFiles 会返回 404。这里把
    「不是接口、也不是静态资源」的 404 改写成 index.html，交给前端路由处理。

    两条例外必须保留 404，否则真故障会被伪装成一个看起来正常的页面：
    1. 原本带 /api 前缀的请求（接口写错时该看到 404，而不是一整页 HTML）；
    2. 带静态资源后缀的请求（缺 JS/CSS 就该报错，便于定位构建产物问题）。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            original: str = str(scope.get("aidrobe_original_path", ""))
            if original == "/api" or original.startswith("/api/"):
                raise
            if Path(path).suffix.lower() in _ASSET_SUFFIXES:
                raise
            index = Path(self.directory or "") / "index.html"
            if not index.is_file():
                raise
            return FileResponse(index, media_type="text/html")


# 前端构建产物：有 dist 就直接托管，没有就跳过（只提供 API + /docs）。
_WEB_DIR: Path = BASE_DIR / "frontend" / "dist"
if (_WEB_DIR / "index.html").is_file():
    app.mount(
        "/",
        _SPAStaticFiles(directory=str(_WEB_DIR), html=True),
        name="web",
    )
    logger.info("已托管前端页面（含 SPA 路由回退）：%s", _WEB_DIR)
else:
    logger.warning(
        "未找到前端构建产物 %s，只提供 API 与 /docs。"
        "在 frontend 目录执行 npm run build 后重启即可。",
        _WEB_DIR,
    )
