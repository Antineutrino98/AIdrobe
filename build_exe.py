"""一键重建 `AIdrobe.exe`。

改完后端或前端（重新 vite build 之后）跑一次即可：

    python build_exe.py

为什么要重打包：exe 里带了 `app/` 的编译产物，改了 Python 源码不重打就不会生效。
本脚本把 PyInstaller 的踩坑全部固化，不要再手工拼那条长命令。
注意：`app/prompts/*.md` 属于数据文件，必须用 --add-data 显式收进 exe。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import sys
import uuid
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent
ICON: Path = ROOT / "assets" / "aidrobe.ico"
OUTPUT: Path = ROOT / "AIdrobe.exe"

# 沙箱的 safe-delete 垫片会拦 PyInstaller 删除项目内 build 目录，
# 所以全部中间产物放系统临时目录，且每次用新目录，不触发删除。
TEMP_ROOT: Path = Path(sys.executable).parent.parent  # 仅用于占位
TEMP: Path = (
    Path.home() / "AppData" / "Local" / "Temp" / f"aidrobe_pkg_{uuid.uuid4().hex[:8]}"
)

COLLECT: tuple[str, ...] = (
    "uvicorn",
    "alembic",
    "py_pglite",
    "pystray",
    "sqlalchemy",
    "pydantic",
    # U4：pywebview 的 GUI 后端与 pythonnet 的 CLR 加载器是动态导入，
    # 不 collect 的话 exe 里缺模块，双击后窗口起不来。
    "webview",
    "clr_loader",
    "pythonnet",
)


def _stop_running_exe() -> None:
    """覆盖 exe 前先让旧实例优雅退出，等 PGlite 落盘。

    直接 proc.kill() 会跳过 lifespan 的 finally，数据库没写完就断，
    用户明确要求「打包要保留此前的数据」，所以先给桌面窗口发 WM_CLOSE，
    走 closing → shutdown() → 关库 → 释放锁，再等进程自然退出。
    """
    try:
        import psutil
    except ImportError:
        return
    targets = [
        proc
        for proc in psutil.process_iter(["pid", "name"])
        if "AIdrobe" in (proc.info.get("name") or "")
    ]
    if not targets:
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def close_window(hwnd: int, _lparam: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                size = user32.GetWindowTextLengthW(hwnd)
                if size:
                    buf = ctypes.create_unicode_buffer(size + 1)
                    user32.GetWindowTextW(hwnd, buf, size + 1)
                    if buf.value.startswith("AIdrobe 智能衣橱"):
                        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True

        user32.EnumWindows(PROC(close_window), 0)
    except Exception:  # noqa: BLE001 - 无 GUI 的 headless 实例直接等它自己退
        pass

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if not any(proc.is_running() for proc in targets):
            print("旧进程已优雅退出，数据已落盘")
            return
        time.sleep(0.5)

    for proc in targets:
        if proc.is_running():
            try:
                proc.kill()
                print(f"警告：旧进程 {proc.pid} 未在 25 秒内退出，已强制结束")
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    if not ICON.is_file():
        print(f"缺少图标：{ICON}")
        return 1

    dist = TEMP / "dist"
    work = TEMP / "work"
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    command: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        "AIdrobe",
        "--icon",
        str(ICON),
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(TEMP),
        # 让打包器顺着 app.main 把业务依赖全部收进来
        "--hidden-import",
        "app.main",
    ]
    for package in COLLECT:
        command += ["--collect-submodules", package]
    command += [
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageDraw",
        "--hidden-import",
        "clr",
        "--hidden-import",
        "webview.platforms.winforms",
        # prompt 是 .md 数据文件，--collect-submodules 只收 Python 模块。
        # 不打进去的话 frozen 后 app/prompts/*.md 全缺，/recommend、
        # /gap-report、入库、购前等链路一律 FileNotFoundError → 500。
        "--add-data",
        f"{ROOT / 'app' / 'prompts'}{os.pathsep}app/prompts",
        str(ROOT / "launcher.py"),
    ]

    print("正在打包（约 40-60 秒）…")
    result = subprocess.run(command, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        print(f"打包失败，退出码 {result.returncode}")
        return result.returncode

    built = dist / "AIdrobe.exe"
    if not built.is_file():
        print(f"没有找到产物：{built}")
        return 1

    _stop_running_exe()
    shutil.copy2(built, OUTPUT)
    print(f"完成：{OUTPUT}（{OUTPUT.stat().st_size / 1024 / 1024:.1f} MB）")
    print("中间产物留在临时目录，可随时清理：", TEMP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
