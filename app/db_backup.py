"""PGlite 数据目录的硬链接增量备份（M1）。

Windows 适配（用户 2026-11-05 明确指定）
----------------------------------------
文档原写 `rsync -a --link-dest=...`，但 Windows 没有 rsync。
等价做法（按用户给的写法落地）：

    遍历 data_dir，对每个文件尝试 os.link(src, dst)；
    若 os.link 抛 OSError（跨卷 / 非 NTFS / 文件系统不支持），
    fallback 为 shutil.copy2。

Python 3.2+ 在 NTFS 上支持 os.link（内部走 CreateHardLink）。
硬链接生效时备份不额外占用磁盘；极端情况退化为全量复制，不会失败。

其余行为不变：24 小时间隔、最多保留 7 份、失败只记日志不阻止启动、
备份必须发生在打开数据库之前（由 db_gateway 保证调用顺序）。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LAST_BACKUP_FILE: str = ".last_backup_at"
BACKUP_PREFIX: str = "backup-"

# 锁文件由 portalocker 持有且内容为空，不参与快照，也避免 Windows 文件占用问题。
# Node 运行时文件是「程序」不是「数据」，体积可达数十 MB，不该进备份。
EXCLUDED_NAMES: frozenset[str] = frozenset(
    {".lock", "package.json", "package-lock.json", "pglite_manager.js"}
)
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({"node_modules"})


def _backup_dirs(backup_dir: Path) -> list[Path]:
    """按目录名排序（时间戳格式保证字典序 == 时间序）。"""
    return sorted(
        (
            p
            for p in backup_dir.iterdir()
            if p.is_dir() and p.name.startswith(BACKUP_PREFIX)
        ),
        key=lambda p: p.name,
    )


def _link_or_copy(src: Path, dst: Path) -> bool:
    """优先硬链接，失败回退复制。返回 True 表示硬链接生效。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return True
    except OSError as exc:
        logger.warning(
            "硬链接不可用，回退为复制：%s -> %s（%s）", src, dst, exc
        )
        shutil.copy2(src, dst)
        return False


def maybe_backup(
    data_dir: str,
    backup_dir: str,
    interval_hours: int = 24,
    keep: int = 7,
) -> str | None:
    """距上次备份不足 interval_hours 则跳过；否则做一份硬链接增量快照。

    返回新备份目录路径；跳过或失败返回 None。失败只记日志、不抛异常，
    因为 AIdrobe.md 明确要求「备份失败不阻止启动」。
    """
    src_root = Path(data_dir)
    dst_root = Path(backup_dir)

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
        marker = dst_root / LAST_BACKUP_FILE
        now = time.time()
        if marker.is_file():
            try:
                last = float(marker.read_text(encoding="utf-8").strip())
                if now - last < interval_hours * 3600:
                    logger.info("距上次备份不足 %s 小时，跳过备份。", interval_hours)
                    return None
            except (ValueError, OSError) as exc:
                logger.warning("读取 %s 失败，按「需要备份」处理：%s", marker, exc)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = dst_root / f"{BACKUP_PREFIX}{stamp}"
        target.mkdir(parents=True, exist_ok=False)

        linked = 0
        copied = 0
        if src_root.is_dir():
            for src in sorted(src_root.rglob("*")):
                if not src.is_file() or src.name in EXCLUDED_NAMES:
                    continue
                rel = src.relative_to(src_root)
                if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
                    continue
                if _link_or_copy(src, target / rel):
                    linked += 1
                else:
                    copied += 1
        logger.info("备份完成：%s（硬链接 %s 个，复制 %s 个）", target, linked, copied)

        # 先裁剪再写时间戳：即使裁剪失败也不影响本次快照已经落盘。
        dirs = _backup_dirs(dst_root)
        for old in (dirs[:-keep] if keep > 0 else dirs):
            shutil.rmtree(old, ignore_errors=True)
            logger.info("删除超出保留数的旧备份：%s", old)

        marker.write_text(str(now), encoding="utf-8")
        return str(target)
    except Exception as exc:  # noqa: BLE001 - 备份失败绝不能阻止启动
        logger.warning("备份失败（不阻止启动）：%s: %s", type(exc).__name__, exc)
        return None


def latest_backup(backup_dir: str) -> Path | None:
    """返回最新一份备份目录；不存在返回 None（供 WAL 错误信息使用）。"""
    root = Path(backup_dir)
    if not root.is_dir():
        return None
    dirs = _backup_dirs(root)
    return dirs[-1] if dirs else None
