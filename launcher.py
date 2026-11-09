"""AIdrobe 桌面启动器（U4：彻底客户端化）。

双击运行后：
1. 单实例检查：已有实例就发 `SHOW` 信号唤起它的窗口，自己立刻退出；
2. 后台线程启动 FastAPI（uvicorn 生产模式，无 --reload），等数据库就绪；
3. 主线程 `webview.start()` 打开独立桌面窗口（没有地址栏/书签栏）；
4. `pystray` 用 `run_detached()` 在后台线程常驻，启动时发唤醒气泡；
5. 点窗口右上角 X → `window.events.closed` → 全局幂等 `shutdown()`
   → 停 uvicorn → lifespan finally → 关 PGlite → 释放文件锁。

U4 裁决 2 已废弃心跳杀进程：退出唯一主责归窗口关闭事件。
U4 裁决 3 已废除前端 Header 传 Key，Key 统一存后端 `.env`。

用法
----
- 双击 `AIdrobe.exe`（推荐，PyInstaller --windowed，无命令行黑框）
- 或 `python launcher.py`
- 隔离测试：`AIDROBE_HEADLESS=1 AIDROBE_PORT=8011 python launcher.py`
  （只起服务，不建窗口/托盘，供自动化测试用）

环境变量
--------
- `AIDROBE_PORT`：HTTP 端口，默认 8000；单实例锁端口 = 端口 + 1000
- `AIDROBE_HEADLESS=1`：无界面测试模式（不建窗口与托盘）
- `AIDROBE_NO_TRAY=1`：建窗口但不建托盘（调试用）
"""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _project_root() -> Path:
    """定位项目根：打包成 exe 后是 exe 所在目录，源码运行时是本文件目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR: Path = _project_root()
LOG_DIR: Path = BASE_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "aidrobe.log"

DEFAULT_PORT: int = 8000
SINGLETON_PORT_OFFSET: int = 1000
READY_TIMEOUT_SECONDS: float = 120.0
WAKE_MESSAGE: str = "AIdrobe 正在唤醒衣橱，请稍候..."
ALREADY_RUNNING_MESSAGE: str = "AIdrobe 已在运行，已为你打开窗口"
SAVING_MESSAGE: str = "正在保存衣橱数据并安全退出..."

logger: logging.Logger = logging.getLogger("aidrobe.launcher")

# ------------------------------------------------------- 全局状态（幂等退出用）
_STATE_LOCK = threading.Lock()
_SHUTDOWN_DONE: bool = False
# closed 事件可能由 pywebview 的另一个线程触发；主线程必须等清理真正
# 跑完才能 os._exit，否则 PGlite 来不及落盘。
_SHUTDOWN_FINISHED = threading.Event()
_QUITTING = threading.Event()
_FORCE_DESTROY = threading.Event()
_SERVER: object | None = None
_THREAD: threading.Thread | None = None
_WINDOW: object | None = None
_TRAY_ICON: object | None = None
_SINGLETON_SOCK: socket.socket | None = None


# --------------------------------------------------------------------- 工具
def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)


def _message_box(text: str, title: str = "AIdrobe", error: bool = False) -> None:
    """Windows 弹窗；非 Windows 或调用失败时退回日志。"""
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:  # noqa: BLE001
        logger.info("%s: %s", title, text)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _is_our_service(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/openapi.json", timeout=2.0
        ) as response:
            payload = json.load(response)
        return str(payload.get("info", {}).get("title", "")) == "AIdrobe"
    except Exception:  # noqa: BLE001
        return False


def _pick_port(preferred: int) -> int:
    for candidate in range(preferred, preferred + 20):
        if not _port_in_use(candidate):
            return candidate
    raise RuntimeError(f"{preferred}-{preferred + 19} 端口都被占用，无法启动")


def _wait_until_ready(port: int, timeout: float) -> bool:
    """等 `/users` 能返回（说明库、迁移都好了）。"""
    deadline = time.time() + timeout
    probe = f"http://127.0.0.1:{port}/users"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(probe, timeout=3.0) as response:
                response.read()
                return True
        except urllib.error.HTTPError:
            time.sleep(0.5)
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def _make_icon() -> "object":
    """画一个简单的衣架图标，避免额外资源文件。"""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(79, 70, 229, 255))
    draw.line((32, 16, 32, 22), fill="white", width=4)
    draw.polygon([(32, 20), (14, 34), (50, 34)], fill="white")
    draw.rectangle((14, 34, 50, 52), fill="white")
    return image


def _install_thread_excepthook() -> None:
    """后台线程崩溃时写日志（--windowed 打包后 stderr 是 None）。"""

    def hook(args: "threading.ExceptHookArgs") -> None:  # type: ignore[name-defined]
        logger.error(
            "后台线程异常：%s: %s",
            args.exc_type.__name__ if args.exc_type else "Unknown",
            args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = hook


def _load_app() -> "object | None":
    """动态导入 FastAPI 应用；失败立刻报出来，而不是等 120 秒超时。"""
    import importlib

    try:
        return importlib.import_module("app.main").app
    except Exception as exc:  # noqa: BLE001
        logger.exception("导入应用失败：%s", exc)
        _message_box(
            f"应用加载失败：{exc}\n\n详细日志：\n{LOG_FILE}",
            "AIdrobe 启动失败",
            error=True,
        )
        return None


# ------------------------------------------------------------ 单实例（裁决 6）
def _singleton_port(http_port: int) -> int:
    return int(
        os.environ.get("AIDROBE_SINGLETON_PORT", str(http_port + SINGLETON_PORT_OFFSET))
    )


def _signal_existing_instance(http_port: int) -> bool:
    """二次启动：向已有实例发 SHOW，让它把窗口前置。"""
    port = _singleton_port(http_port)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8) as sock:
            sock.sendall(b"SHOW\n")
        return True
    except OSError:
        return False


def _bind_singleton(http_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", _singleton_port(http_port)))
    sock.listen(8)
    sock.settimeout(1.0)
    return sock


def _start_singleton_listener(sock: socket.socket) -> None:
    def loop() -> None:
        while not _SHUTDOWN_DONE:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                payload = conn.recv(64)
            except OSError:
                payload = b""
            finally:
                conn.close()
            if b"SHOW" not in payload:
                continue
            window = _WINDOW
            if window is None:
                logger.info("窗口尚未就绪，忽略本次唤醒信号")
                continue
            try:
                window.restore()  # type: ignore[attr-defined]
                logger.info("已响应唤醒信号，窗口前置")
            except Exception as exc:  # noqa: BLE001
                logger.warning("前置窗口失败：%s", exc)

    threading.Thread(
        target=loop, name="aidrobe-singleton", daemon=True
    ).start()


# -------------------------------------------------------------- 系统通知 / 托盘
def _system_notice(message: str, *, visible_seconds: float = 4.0) -> None:
    """短生命周期托盘图标发一条 Windows 气泡；失败退回弹框。"""
    try:
        import pystray

        if not getattr(pystray.Icon, "HAS_NOTIFICATION", False):
            raise RuntimeError("当前平台不支持托盘通知")
        icon = pystray.Icon("aidrobe-notice", _make_icon(), "AIdrobe 智能衣橱")

        def setup(tray_icon: "object") -> None:
            try:
                tray_icon.visible = True  # type: ignore[attr-defined]
                tray_icon.notify(message, "AIdrobe 智能衣橱")  # type: ignore[attr-defined]
                logger.info("已发送系统通知：%s", message)
                time.sleep(visible_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("系统通知发送失败，改用弹框：%s", exc)
                _message_box(message)
            finally:
                try:
                    tray_icon.visible = False  # type: ignore[attr-defined]
                    tray_icon.stop()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass

        icon.run_detached(setup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("托盘通知不可用，改用弹框：%s", exc)
        _message_box(message)


def _start_tray() -> "object | None":
    """裁决 1：pystray 用 run_detached，主线程留给 webview.start()。"""
    global _TRAY_ICON
    try:
        import pystray
    except ImportError:
        logger.warning("未安装 pystray，跳过托盘（仍可用窗口右上角 X 退出）")
        return None

    def open_window() -> None:
        window = _WINDOW
        if window is not None:
            try:
                window.restore()  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                logger.warning("打开界面失败：%s", exc)

    def quit_app(_icon: "object" = None, _item: "object" = None) -> None:
        logger.info("托盘收到退出请求")
        # 与点 X 完全同一条路径：藏窗口 → 气泡 → 关库 → 退出。
        _begin_exit_flow()

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", open_window, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("彻底退出", quit_app),
    )
    icon = pystray.Icon("aidrobe", _make_icon(), "AIdrobe 智能衣橱", menu)

    def setup(tray_icon: "object") -> None:
        tray_icon.visible = True  # type: ignore[attr-defined]
        try:
            tray_icon.notify(WAKE_MESSAGE, "AIdrobe 智能衣橱")  # type: ignore[attr-defined]
            logger.info("启动气泡：%s", WAKE_MESSAGE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动气泡发送失败：%s", exc)

    icon.run_detached(setup)
    _TRAY_ICON = icon
    return icon


def _notify_saving() -> None:
    """点 X 后给一条托盘气泡，让「正在保存」可见，不让用户以为卡死。"""
    icon = _TRAY_ICON
    if icon is not None:
        try:
            icon.notify(SAVING_MESSAGE, "AIdrobe 智能衣橱")  # type: ignore[attr-defined]
            logger.info("退出气泡：%s", SAVING_MESSAGE)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("托盘退出气泡失败：%s", exc)
    # 没建常驻托盘时用一个短生命周期图标兜底。
    _system_notice(SAVING_MESSAGE, visible_seconds=2.0)


def _begin_exit_flow() -> None:
    """U5：关闭窗口 → 藏到托盘 + 气泡 → 后台关库 → 关完才真正结束进程。

    关库必须跑完（绝不牺牲数据安全换速度），但把窗口先隐藏并把进度告诉用户，
    避免十几秒的黑屏看起来像卡死。
    """
    if _QUITTING.is_set():
        return
    _QUITTING.set()
    window = _WINDOW
    if window is not None:
        try:
            window.hide()  # type: ignore[attr-defined]  # 最小化/隐藏到系统托盘
            logger.info("窗口已隐藏到系统托盘")
        except Exception as exc:  # noqa: BLE001
            logger.warning("隐藏窗口失败：%s", exc)
    _notify_saving()

    def work() -> None:
        shutdown()
        if getattr(sys, "frozen", False):
            _SHUTDOWN_FINISHED.wait(timeout=30)
            logging.shutdown()
            os._exit(0)
        elif window is not None:
            # 源码模式：销毁窗口让 webview.start() 返回，走 main 的 finally。
            _FORCE_DESTROY.set()  # 这次的 destroy 必须被允许
            try:
                window.destroy()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=work, name="aidrobe-exit", daemon=True).start()


# ------------------------------------------------------------------ 幂等退出
def shutdown() -> None:
    """裁决 2：全局唯一、幂等的优雅退出入口。

    停 uvicorn → lifespan finally → close_database → 释放 flock，
    再关掉单实例 socket 与托盘，确保任务管理器里不留进程。
    """
    global _SHUTDOWN_DONE
    with _STATE_LOCK:
        if _SHUTDOWN_DONE:
            # 窗口事件线程正在关库，等它完成，不能抢先退出。
            _SHUTDOWN_FINISHED.wait(timeout=30)
            return
        _SHUTDOWN_DONE = True
        server, thread = _SERVER, _THREAD

    try:
        logger.info("收到退出请求，正在关闭…")
        if server is not None:
            try:
                server.should_exit = True  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if thread is not None:
            thread.join(timeout=30)
            if thread.is_alive():
                logger.warning("服务未在 30 秒内退出，可能仍在写入数据库")
            else:
                logger.info("服务已停止，数据库已关闭")
        if _SINGLETON_SOCK is not None:
            try:
                _SINGLETON_SOCK.close()
            except OSError:
                pass
        if _TRAY_ICON is not None:
            try:
                _TRAY_ICON.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    finally:
        _SHUTDOWN_FINISHED.set()


# --------------------------------------------------------------------- 主流程
def _ensure_env_file() -> None:
    """首次运行时从 .env.example 生成空 Key 的 .env。

    GitHub Release 的 ZIP 只带 .env.example（绝不含真实 Key）。用户解压后
    直接双击 exe，这里补一份可运行的空配置，Key 由用户在设置页填写；
    已有 .env 的环境完全不受影响。
    """
    env_file: Path = BASE_DIR / ".env"
    if env_file.is_file():
        return
    example: Path = BASE_DIR / ".env.example"
    if not example.is_file():
        return
    try:
        env_file.write_bytes(example.read_bytes())
        logger.info("未找到 .env，已从 .env.example 生成空 Key 配置，请到设置页填写")
    except OSError:
        logger.warning("生成 .env 失败，将继续以默认配置尝试启动", exc_info=True)


def main() -> int:
    global _SERVER, _THREAD, _WINDOW, _SINGLETON_SOCK

    _setup_logging()
    _install_thread_excepthook()
    os.chdir(BASE_DIR)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    _ensure_env_file()

    preferred = int(os.environ.get("AIDROBE_PORT", str(DEFAULT_PORT)))

    # 裁决 6：已有实例 → 发 SHOW 唤起窗口，不启动第二个服务。
    if _signal_existing_instance(preferred):
        logger.info("已有 AIdrobe 实例，已发送 SHOW 信号")
        _system_notice(ALREADY_RUNNING_MESSAGE)
        return 0

    try:
        _SINGLETON_SOCK = _bind_singleton(preferred)
    except OSError as exc:
        _message_box(
            f"无法建立单实例锁（端口 {_singleton_port(preferred)}）：{exc}",
            "AIdrobe 无法启动",
            error=True,
        )
        return 1
    _start_singleton_listener(_SINGLETON_SOCK)

    # 兼容：端口被别的程序占用时顺延找空位。
    if _port_in_use(preferred) and _is_our_service(preferred):
        logger.info("端口 %d 上已有 AIdrobe 服务但无单实例锁，直接退出", preferred)
        return 0
    try:
        port = _pick_port(preferred)
    except RuntimeError as exc:
        _message_box(str(exc), "AIdrobe 无法启动", error=True)
        return 1

    url = f"http://127.0.0.1:{port}"
    logger.info("正在启动 AIdrobe：%s", url)

    fastapi_app = _load_app()
    if fastapi_app is None:
        return 1

    import uvicorn

    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=20,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="aidrobe-server", daemon=True)
    thread.start()
    _SERVER, _THREAD = server, thread

    if not _wait_until_ready(port, READY_TIMEOUT_SECONDS):
        logger.error("等待服务就绪超时，详见 %s", LOG_FILE)
        shutdown()
        _message_box(
            "AIdrobe 启动失败。\n\n常见原因：.env 里的模型配置不完整，"
            f"或数据库被另一个进程占用。\n\n详细日志：\n{LOG_FILE}",
            "AIdrobe 启动失败",
            error=True,
        )
        return 1

    logger.info("服务已就绪")

    # 隔离测试用：只起服务，不建窗口/托盘。
    if os.environ.get("AIDROBE_HEADLESS") == "1":
        logger.info("AIDROBE_HEADLESS=1，无界面模式")
        try:
            while thread.is_alive() and not _SHUTDOWN_DONE:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        shutdown()
        return 0

    if os.environ.get("AIDROBE_NO_TRAY") != "1":
        _start_tray()
    else:
        _system_notice(WAKE_MESSAGE)

    try:
        import webview
    except ImportError as exc:
        logger.exception("pywebview 不可用")
        shutdown()
        _message_box(
            f"桌面窗口组件不可用：{exc}\n\n请确认已安装 pywebview / pythonnet "
            "与 Edge WebView2 Runtime。",
            "AIdrobe 启动失败",
            error=True,
        )
        return 1

    window = webview.create_window(
        title="AIdrobe 智能衣橱",
        url=url,
        width=1280,
        height=800,
        background_color="#F8F9FA",
        min_size=(1024, 720),
    )
    _WINDOW = window
    # 裁决 2 + U5 交互缓冲：closing 返回 False 取消真正的销毁，
    # 让窗口先藏到托盘并给出「正在保存」气泡；closed 作为平台不支持
    # 取消关闭时的兜底（此时窗口已消失，但托盘气泡和关库流程照跑）。
    def on_closing() -> bool:
        if _FORCE_DESTROY.is_set():
            return True  # 关库完成后主动销毁，别再拦
        _begin_exit_flow()
        return False

    def on_closed(*_args: object) -> None:
        _begin_exit_flow()

    try:
        window.events.closing += on_closing  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.info("当前 pywebview 不支持 closing 事件，使用 closed 兜底：%s", exc)
    window.events.closed += on_closed  # type: ignore[attr-defined]

    # U5 用户反馈：pywebview 的 private_mode 默认 True，每次启动都是全新的
    # WebView2 存储区，localStorage 全被清空 —— 用户、模型选择、衣橱范围、
    # 「稍后配置」标记每次都要重设。关掉隐私模式并固定存储目录，
    # 让这些选择随程序保留；Key 本来就存在后端 .env，两者互不冲突。
    storage_dir = os.path.join(os.getcwd(), "data", "webview")
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except OSError as exc:
        logger.warning("WebView 存储目录创建失败，仍尝试启动：%s", exc)
        storage_dir = None

    try:
        webview.start(private_mode=False, storage_path=storage_dir)  # 阻塞主线程
    except Exception as exc:  # noqa: BLE001
        logger.exception("桌面窗口异常退出：%s", exc)
        _message_box(
            f"桌面窗口启动失败：{exc}\n\n请确认已安装 Edge WebView2 Runtime。",
            "AIdrobe 启动失败",
            error=True,
        )
    finally:
        shutdown()
    # 桌面窗口关闭后，pythonnet / WinForms 与 PyInstaller onefile 的父进程
    # 有时还要拖十几秒才退干净。关键清理（uvicorn、PGlite、锁、托盘、
    # 单实例 socket、日志）已在 shutdown() 里全部完成，这里只在打包成 exe
    # 时强制收尾，保证任务管理器里几秒内不留进程。
    if getattr(sys, "frozen", False):
        _SHUTDOWN_FINISHED.wait(timeout=30)
        logging.shutdown()
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
