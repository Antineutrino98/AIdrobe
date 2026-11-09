"""应用配置（M7）。

关于 `pydantic-settings`
------------------------
M7.md 写的是「config.py # pydantic-settings」。当前隔离环境**没有安装**
`pydantic-settings`，`requirements.txt` 也未声明；按 AIdrobe.md「禁止引入未声明
依赖」的规则，本文件用 **pydantic BaseModel + 内置 .env 读取**实现同等能力：

- 配置项有类型、有默认值、可用 `Settings.from_env()` 从环境变量构造；
- `.env` 逐行读取，**已存在的环境变量优先**（不覆盖 shell / CI 注入的值）；
- 不新增任何第三方依赖。

如果以后允许安装 `pydantic-settings`，只需把 `Settings` 换成
`BaseSettings` 并删掉 `load_dotenv()`，外部调用方式不变。

环境变量兼容
------------
M7.md 与前面模块的变量名有几处不一致，这里统一做映射（不覆盖已有值）：
- `IMAGE_DIR` → 模块实际读取的 `IMAGE_STORAGE_DIR`（两者都保留）
- `EMBEDDING_API_BASE` → 模块实际读取的 `EMBEDDING_API_URL`
- `DEFAULT_STRICTNESS`：M7 写 `moderate`，M4 冻结的是
  `conservative` / `permissive`；`moderate` 在 M4 里映射为 `conservative`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel


def _project_root() -> Path:
    """项目根目录。

    源码运行时是本文件的上两级；被 PyInstaller 打包后，`__file__` 指向
    解压出来的临时目录，数据和 `.env` 会落到 `%TEMP%`（重启即丢）。
    所以打包后改用 exe 所在目录——exe 就放在项目根，数据仍然可见。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _project_root()
DEFAULT_STRICTNESS_ENV: str = "DEFAULT_STRICTNESS"


def _env_int(name: str, default: int) -> int:
    raw: str = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_dotenv(path: Path | None = None) -> Path | None:
    """极简 .env 读取：KEY=VALUE，注释与空行跳过，已有环境变量不覆盖。

    返回实际读取的文件路径；没有 .env 时返回 None，不报错。
    """
    env_path: Path = path or (BASE_DIR / ".env")
    if not env_path.is_file():
        return None
    try:
        content: str = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in content.splitlines():
        line: str = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return env_path


class Settings(BaseModel):
    """M7 关心的配置项；模型三角色的变量仍由 llm_config 直接校验。"""

    pglite_data_dir: str = "data/pglite"
    pglite_backup_dir: str = "data/pglite-backups"
    pglite_backup_interval_hours: int = 24
    pglite_backup_keep: int = 7
    image_dir: str = "data/images"
    default_owner_id: str | None = None
    default_strictness: str = "conservative"
    weather_api_base: str = "https://api.open-meteo.com/v1"

    @classmethod
    def from_env(cls) -> "Settings":
        """先读 .env（不覆盖已有变量），再从环境变量构造。"""
        load_dotenv()
        return cls(
            pglite_data_dir=os.environ.get("PGLITE_DATA_DIR", "data/pglite"),
            pglite_backup_dir=os.environ.get(
                "PGLITE_BACKUP_DIR", "data/pglite-backups"
            ),
            pglite_backup_interval_hours=_env_int(
                "PGLITE_BACKUP_INTERVAL_HOURS", 24
            ),
            pglite_backup_keep=_env_int("PGLITE_BACKUP_KEEP", 7),
            image_dir=os.environ.get(
                "IMAGE_STORAGE_DIR", os.environ.get("IMAGE_DIR", "data/images")
            ),
            default_owner_id=(
                os.environ.get("AIDROBE_DEFAULT_OWNER_ID", "").strip() or None
            ),
            default_strictness=(
                os.environ.get(DEFAULT_STRICTNESS_ENV, "conservative").strip()
                or "conservative"
            ),
            weather_api_base=os.environ.get(
                "WEATHER_API_BASE", "https://api.open-meteo.com/v1"
            ).rstrip("/"),
        )

    def apply_env_compat(self) -> None:
        """把 M7 变量名映射到各模块实际读取的变量名（已有值不覆盖）。"""
        os.environ.setdefault("IMAGE_STORAGE_DIR", self.image_dir)
        os.environ.setdefault("IMAGE_DIR", self.image_dir)
        os.environ.setdefault(
            "PGLITE_BACKUP_INTERVAL_HOURS",
            str(self.pglite_backup_interval_hours),
        )
        os.environ.setdefault("PGLITE_BACKUP_KEEP", str(self.pglite_backup_keep))
        os.environ.setdefault(
            DEFAULT_STRICTNESS_ENV, self.default_strictness
        )
        os.environ.setdefault("WEATHER_API_BASE", self.weather_api_base)
        if self.default_owner_id:
            os.environ.setdefault(
                "AIDROBE_DEFAULT_OWNER_ID", self.default_owner_id
            )
        # 兼容 M7 的 EMBEDDING_API_BASE 与模块实际使用的 EMBEDDING_API_URL。
        if not os.environ.get("EMBEDDING_API_URL", "").strip():
            base: str = os.environ.get("EMBEDDING_API_BASE", "").strip()
            if base:
                os.environ["EMBEDDING_API_URL"] = base


def get_settings() -> Settings:
    """每次调用都重新读环境变量（便于测试 monkeypatch.setenv）。"""
    return Settings.from_env()
