"""打开 PGlite 时的 WAL 错误识别（M1 交付物：db_recovery.py）。

与 M1.md 的差异（用户 2026-11-05 明确指示，以新指令为准）
----------------------------------------------------------
M1.md 原文要求「把 pg_wal 重命名为 pg_wal.corrupt-{timestamp} 后重试一次」。
用户在本轮明确改为：**不做任何修复动作，检测到 WAL 错误直接抛
`DatabaseUnrecoverableError`**。理由：PGlite 的 WAL 与原生 PostgreSQL 语义
不同，自动重命名既不可靠也可能掩盖真实损坏；交给人工按 README 的
「手动恢复步骤」处理更安全。

因此本模块：
- 不重命名任何目录；
- 不调用 pg_resetwal；
- 不自动从备份恢复；
- 抛出的异常消息包含错误详情、WAL 目录路径、备份目录位置，方便人工判断。

底层后端差异一律由 `db_gateway.open_raw(data_dir)` 负责，
本模块不出现 `PGlite.create()` 这类 JS API（用户 q-0 的架构要求）。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.db_backup import latest_backup
from app.db_gateway import DatabaseUnrecoverableError, open_raw

if TYPE_CHECKING:
    from app.db_gateway import RawDatabase

logger = logging.getLogger(__name__)

# 判定为 WAL / crash-recovery 相关故障的关键词。
# 注意：不能把裸 "aborted" / "recovery" 当关键词 —— 实测中
# "Some operations were aborted"（无关的文件操作错误）会被误判成 WAL 损坏，
# 从而把普通启动失败伪装成不可恢复错误。这里只认 WAL 特有表述。
WAL_ERROR_MARKERS: tuple[str, ...] = (
    "wal",
    "pg_wal",
    "xlog",
    "replay",
    "crash recovery",
    "crash-recovery",
    "corrupt",
    "checkpoint record",
    "panicked",
)
# PostgreSQL 原始错误类名 Aborted()，需要精确到括号形式。
ABORTED_PATTERN = re.compile(r"\baborted\(\)", re.IGNORECASE)
README_RECOVERY_ANCHOR: str = "README.md#手动恢复步骤"


def _is_wal_error(exc: BaseException) -> bool:
    """异常链里任一层消息命中 WAL 关键词即认为是 WAL 相关故障。"""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__}: {current}".lower()
        if any(marker in text for marker in WAL_ERROR_MARKERS):
            return True
        if ABORTED_PATTERN.search(text):
            return True
        current = current.__cause__ or current.__context__
    return False


async def open_with_wal_recovery(data_dir: str) -> RawDatabase:
    """打开数据库；WAL 相关故障一律转为明确的不可恢复异常。

    返回 `RawDatabase`（底层后端句柄）。业务代码拿到的仍是
    `db_gateway.DatabaseHandle`，不会接触这个对象。
    """
    try:
        return await open_raw(data_dir)
    except DatabaseUnrecoverableError:
        raise
    except Exception as exc:  # noqa: BLE001 - 需要把底层异常翻译成人可读错误
        if not _is_wal_error(exc):
            raise
        backup_dir = os.environ.get("PGLITE_BACKUP_DIR", "data/pglite-backups")
        latest = latest_backup(backup_dir)
        latest_text: str = str(latest) if latest is not None else "（暂无可用备份）"
        message = (
            "检测到 PGlite 的 WAL / crash-recovery 相关错误，已停止启动"
            "（按约定不自动修复、不重命名、不删除任何目录）。\n"
            f"  原始错误：{type(exc).__name__}: {exc}\n"
            f"  数据目录 data_dir：{Path(data_dir).resolve()}\n"
            f"  最新备份位置：{latest_text}\n"
            f"  手动恢复步骤：{README_RECOVERY_ANCHOR}\n"
            "  现场已完整保留，未重命名、未 pg_resetwal、未自动从备份恢复。"
        )
        logger.error("%s", message)
        raise DatabaseUnrecoverableError(message) from exc
