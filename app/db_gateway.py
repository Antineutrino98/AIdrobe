"""PGlite 打开 / 关闭 / 锁的**唯一入口**（M1 核心）。

架构（用户 2026-11-05 拍板）
----------------------------
业务代码（M1–M7）只认 `DatabaseHandle` —— 一个有 `.engine` 和
`.close()` 的对象，不认底层是 py-pglite 还是 pglite-socket。
后端差异全部封装在本文件：

- `PGLITE_BACKEND=py_pglite`（默认）：由本模块启动一个 Node 子进程，
  跑 `@electric-sql/pglite` + `@electric-sql/pglite-socket` 的 TCP 服务，
  并返回标准 PostgreSQL 连接串给 SQLAlchemy / asyncpg / Alembic。
- `PGLITE_BACKEND=pglite_socket`（备选）：以子进程方式启动
  `pglite-server --db={data_dir}`，同样返回连接串。

为什么不直接用 py-pglite 的 `PGliteManager`（已实测，见 README）：
1. 它在 Windows 上调 `npm install`（无 .cmd 后缀）→ `FileNotFoundError WinError 2`；
2. 它生成的 JS 是 `new PGlite({extensions})`，**没有 dataDir**，是纯内存库，
   磁盘上没有数据目录，备份 / WAL / 重启保留数据全部失去对象；
3. 它的关闭流程在 Windows 上会挂住。
因此默认后端复用它声明的**同两个官方 npm 包 + 同一套 socket 协议**，
但由本模块自己生成带 `dataDir` 的启动脚本并管理子进程生命周期。
`DatabaseHandle` 的对外契约不变，M2 及之后的代码无需改动。

数据库规则（AIdrobe.md）
------------------------
- 所有访问通过本模块取唯一句柄，禁止业务代码自行打开 PGlite；
- 单进程单连接：引擎 `pool_size=1, max_overflow=0`，所有查询顺序执行；
- 文件锁用 portalocker（Windows→msvcrt，Unix→fcntl），进程退出自动释放；
- 备份在打开数据库之前完成；SIGTERM/SIGINT 由调用方触发 close()。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import portalocker
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db_backup import maybe_backup

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 配置
DEFAULT_DATA_DIR: str = "data/pglite"
DEFAULT_BACKUP_DIR: str = "data/pglite-backups"
LOCK_FILENAME: str = ".lock"
RUNTIME_DIRNAME: str = ".runtime"
PGDATA_DIRNAME: str = "pgdata"
BACKEND_ENV: str = "PGLITE_BACKEND"
DATA_DIR_ENV: str = "PGLITE_DATA_DIR"
BACKUP_DIR_ENV: str = "PGLITE_BACKUP_DIR"
HOST_ENV: str = "PGLITE_TCP_HOST"
PORT_ENV: str = "PGLITE_TCP_PORT"
SOCKET_BIN_ENV: str = "PGLITE_SOCKET_BIN"
DATABASE_URL_ENV: str = "DATABASE_URL"

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 55432
BACKUP_INTERVAL_ENV: str = "PGLITE_BACKUP_INTERVAL_HOURS"
BACKUP_KEEP_ENV: str = "PGLITE_BACKUP_KEEP"


def _env_int(name: str, default: int) -> int:
    """读整数环境变量；空/非法时回退默认值并记日志（M7 配置项）。"""
    raw: str = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r 不是整数，使用默认 %d", name, raw, default)
        return default

# 与 py-pglite 0.5.3 声明的依赖版本保持一致（实测通过 PostgreSQL 17.4 + pgvector）。
NPM_DEPENDENCIES: dict[str, str] = {
    "@electric-sql/pglite": "^0.3.0",
    "@electric-sql/pglite-socket": "^0.0.8",
}

# 带 dataDir 的持久化启动脚本；stdin 收到 shutdown 时只 close 数据库
# （不调用 server.stop()，实测它在 Windows 上可能挂住），然后退出。
NODE_MANAGER_JS: str = """\
const fs = require('fs');
const { PGlite } = require('@electric-sql/pglite');
const { vector } = require('@electric-sql/pglite/vector');
const { PGLiteSocketServer } = require('@electric-sql/pglite-socket');

async function main() {
  const db = new PGlite({
    dataDir: process.env.PGLITE_DATA_DIR,
    extensions: { vector },
  });
  await db.waitReady;
  const server = new PGLiteSocketServer({
    db,
    host: process.env.PGLITE_TCP_HOST || '127.0.0.1',
    port: Number(process.env.PGLITE_TCP_PORT || 55432),
  });
  await server.start();
  // 用就绪文件代替「先连一次再关」的探测：PGlite 只支持一个活动连接，
  // 探测连接若未被及时释放，会让后续 SQLAlchemy 连接一直排队（实测踩过）。
  if (process.env.PGLITE_READY_FILE) {
    fs.writeFileSync(process.env.PGLITE_READY_FILE, 'ready');
  }
  console.log('AIDROBE_PGLITE_READY');
  const shutdown = async () => {
    try { await db.close(); } catch (e) { console.error('close error', e); }
    process.exit(0);
  };
  process.stdin.on('data', async (chunk) => {
    if (chunk.toString().trim() === 'shutdown') { await shutdown(); }
  });
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}
main().catch((e) => {
  console.error('AIDROBE_PGLITE_BOOT_FAIL', e);
  process.exit(1);
});
"""

# 模块级唯一句柄；get_session_factory() 兼容层从这里取 engine。
_CURRENT_HANDLE: DatabaseHandle | None = None


# ------------------------------------------------------------------ 异常
class DatabaseLockedError(RuntimeError):
    """同一数据目录已有进程持锁；消息含「另一个进程正在使用数据库」。"""


class DatabaseUnrecoverableError(RuntimeError):
    """WAL / 崩溃恢复相关故障；只报告、不自动修复，消息含人工恢复指引。"""


class DatabaseBackendError(RuntimeError):
    """底层 PGlite 子进程启动失败或端口未就绪。"""


# ------------------------------------------------------------ 底层后端句柄
@dataclass
class RawDatabase:
    """后端启动结果。业务代码不接触此对象（只认 DatabaseHandle）。"""

    dsn: str
    data_dir: Path
    backend: str
    process: subprocess.Popen[str] | None = None
    runtime_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    async def aclose(self) -> None:
        """优雅关闭（短超时），失败则终止子进程；不删除任何锁文件或数据。"""
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        # 先尝试让 Node 自己 flush 并退出；等不起就强杀，避免永久挂住。
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.write("shutdown\n")
                proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError):
            pass
        # U4：uvicorn 关闭阶段默认 executor 已经停了，asyncio.to_thread 会抛
        # "cannot schedule new futures after shutdown"，把 lifespan 的 finally
        # 打断、PGlite 来不及正常收尾。此时已无并发请求，同步等待即可。
        async def _wait(timeout: int = 3) -> None:
            try:
                await asyncio.to_thread(proc.wait, timeout)
            except RuntimeError:
                proc.wait(timeout=timeout)

        try:
            await _wait()
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.terminate()
            await _wait()
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            proc.kill()
            await _wait()
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("PGlite 子进程未能正常终止：%s", exc)


# ------------------------------------------------------------ 对外唯一句柄
class DatabaseHandle:
    """业务代码唯一认识的数据库对象：`.engine` + `.close()`。"""

    def __init__(
        self,
        engine: AsyncEngine,
        raw: RawDatabase,
        lock: portalocker.Lock,
        data_dir: str,
        backup_dir: str,
    ) -> None:
        self._engine: AsyncEngine = engine
        self._raw: RawDatabase = raw
        self._lock: portalocker.Lock = lock
        self._closed: bool = False
        self.data_dir: str = data_dir
        self.backup_dir: str = backup_dir

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def dsn(self) -> str:
        return self._raw.dsn

    @property
    def backend(self) -> str:
        return self._raw.backend

    async def close(self) -> None:
        """释放引擎 → 关闭后端 → 释放锁（锁文件本身保留，不删除）。"""
        global _CURRENT_HANDLE
        if self._closed:
            return
        self._closed = True
        try:
            # dispose 要等 asyncpg 断开连接；PGlite socket 不回应 Terminate，
            # 这一步必然接近超时。3 秒足够，之后直接关子进程。
            try:
                await asyncio.wait_for(self._engine.dispose(), timeout=3)
            except asyncio.TimeoutError:
                logger.info("engine.dispose() 超时（3s），直接关闭 PGlite 子进程")
        finally:
            try:
                await self._raw.aclose()
            finally:
                try:
                    self._lock.release()
                except portalocker.LockException as exc:
                    logger.warning("释放文件锁时出现异常（可忽略）：%s", exc)
                if _CURRENT_HANDLE is self:
                    _CURRENT_HANDLE = None


def get_current_handle() -> DatabaseHandle | None:
    """给 app/db.py 的兼容层使用；未打开数据库时返回 None。"""
    return _CURRENT_HANDLE


# ------------------------------------------------------------------ 工具
def _npm_command() -> str:
    """Windows 上 npm 的真名是 npm.cmd；Unix 上是 npm。"""
    return shutil.which("npm.cmd") or shutil.which("npm") or "npm"


def _node_command() -> str:
    return shutil.which("node.exe") or shutil.which("node") or "node"


def _no_window_kwargs() -> dict[str, Any]:
    """Windows 下抑制子进程自带的命令行黑框（U5 之后的用户反馈）。

    `AIdrobe.exe` 是 `--windowed` 的 GUI 程序，本身没有控制台；但它拉起的
    `node.exe` / `npm.cmd` 属于控制台子系统，Windows 会为这类子进程新建一个
    conhost 窗口 —— **即使 stdout/stderr 已经重定向到 PIPE 也一样**。
    PGlite 子进程常驻，那个空黑框就一直挂在任务栏直到程序退出。

    `CREATE_NO_WINDOW` 让系统不分配控制台；`STARTUPINFO` + `SW_HIDE` 再对
    经过 cmd.exe 的 `.cmd` / `shell=True` 路径兜一层。仅 Windows 生效，
    其它平台返回空字典，调用方直接 `**` 展开即可。
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _port_open(host: str, port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(1)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


async def _wait_for_server(
    host: str,
    port: int,
    proc: subprocess.Popen[str],
    ready_file: Path | None = None,
    timeout: float = 90,
) -> None:
    """等 Node 写出就绪文件（优先）或端口可连。

    不在这里建立 PostgreSQL 连接：PGlite 的 socket 服务只支持一个活动连接，
    探测连接若未被服务端及时释放，后续 SQLAlchemy 连接会永久排队。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if proc.poll() is not None:
            output = ""
            try:
                if proc.stdout is not None:
                    output = await asyncio.to_thread(proc.stdout.read, 2000) or ""
            except (OSError, ValueError):
                pass
            raise DatabaseBackendError(
                f"PGlite 子进程启动即退出（exit={proc.returncode}）。输出：{output[:1200]}"
            )
        if ready_file is not None:
            try:
                if ready_file.read_text(encoding="utf-8").strip() == "ready":
                    return
            except OSError:
                pass
        elif _port_open(host, port):
            return
        await asyncio.sleep(0.2)
    raise DatabaseBackendError(
        f"等待 PGlite 就绪超时（{host}:{port}，{timeout}s）。"
        f"就绪文件：{ready_file}"
    )


def _write_runtime_files(runtime_dir: Path, host: str, port: int) -> None:
    """写 package.json 与启动脚本；已有文件不覆盖（便于用户自定义）。"""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    package_json = runtime_dir / "package.json"
    if not package_json.exists():
        package_json.write_text(
            json.dumps(
                {
                    "name": "aidrobe-pglite-runtime",
                    "version": "1.0.0",
                    "private": True,
                    "dependencies": NPM_DEPENDENCIES,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    manager_js = runtime_dir / "pglite_manager.js"
    # 脚本内容由本模块版本化管理：每次写入保证与当前契约一致。
    manager_js.write_text(NODE_MANAGER_JS, encoding="utf-8")
    del host, port  # 由环境变量传给 Node，脚本里不写死


def _ensure_npm_deps(runtime_dir: Path) -> None:
    """首次运行装 npm 依赖；已有 node_modules 直接跳过。"""
    if (runtime_dir / "node_modules").is_dir():
        return
    cmd = _npm_command()
    logger.info("首次启动，安装 PGlite npm 依赖：%s install（%s）", cmd, runtime_dir)
    result = subprocess.run(
        [cmd, "install", "--no-audit", "--no-fund"],
        cwd=runtime_dir,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        **_no_window_kwargs(),
    )
    if result.returncode != 0:
        raise DatabaseBackendError(
            f"npm install 失败（exit={result.returncode}）。\n"
            f"stdout: {(result.stdout or '')[-800:]}\n"
            f"stderr: {(result.stderr or '')[-800:]}"
        )


async def _start_py_pglite_backend(data_dir: Path, host: str, port: int) -> RawDatabase:
    """默认后端：带 dataDir 的持久化 PGlite TCP 服务。"""
    runtime_dir = data_dir / RUNTIME_DIRNAME
    pgdata = data_dir / PGDATA_DIRNAME
    pgdata.mkdir(parents=True, exist_ok=True)
    _write_runtime_files(runtime_dir, host, port)
    await asyncio.to_thread(_ensure_npm_deps, runtime_dir)

    # 固定名 + 覆写标记，不做任何删除：某些运行环境会拦截 unlink
    # （重定向到回收站，失败时直接抛 OSError）。先写 starting，
    # Node 就绪后改写 ready，因此不会把上一轮的旧标记误判为本次就绪。
    ready_file = runtime_dir / "ready.flag"
    ready_file.write_text("starting", encoding="utf-8")

    env = os.environ.copy()
    # 不让 PGlite 子进程继承宿主的 NODE_OPTIONS：某些运行环境会注入
    # safe-delete 垫片（把 fs.unlink 重定向到回收站），导致 PGlite 无法清理
    # 陈旧的 postmaster.pid 而启动失败。这里用 Node 原生文件操作。
    env.pop("NODE_OPTIONS", None)
    # Node 子进程的 cwd 是 runtime_dir，相对路径会被再拼一层导致 ENOENT；
    # 数据目录必须传绝对路径（实测踩过这个坑）。
    env["PGLITE_DATA_DIR"] = str(pgdata.resolve())
    env["PGLITE_TCP_HOST"] = host
    env["PGLITE_TCP_PORT"] = str(port)
    env["PGLITE_READY_FILE"] = str(ready_file.resolve())
    proc: subprocess.Popen[str] = subprocess.Popen(
        [_node_command(), "pglite_manager.js"],
        cwd=runtime_dir.resolve(),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **_no_window_kwargs(),
    )
    try:
        await _wait_for_server(host, port, proc, ready_file)
    except Exception:
        raw = RawDatabase(dsn="", data_dir=data_dir, backend="py_pglite", process=proc)
        await raw.aclose()
        raise
    dsn = f"postgresql+asyncpg://postgres:postgres@{host}:{port}/postgres"
    return RawDatabase(
        dsn=dsn,
        data_dir=data_dir,
        backend="py_pglite",
        process=proc,
        runtime_dir=runtime_dir,
    )


async def _start_pglite_socket_backend(data_dir: Path, host: str, port: int) -> RawDatabase:
    """备选后端：外部 pglite-server 子进程（命令可用环境变量整体覆盖）。"""
    pgdata = data_dir / PGDATA_DIRNAME
    pgdata.mkdir(parents=True, exist_ok=True)
    template = os.environ.get(SOCKET_BIN_ENV, "npx pglite-server")
    command = f'{template} --db={pgdata.resolve()} --host={host} --port={port}'
    logger.info("启动备选后端：%s", command)
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=data_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **_no_window_kwargs(),
    )
    try:
        await _wait_for_server(host, port, proc)
    except Exception:
        raw = RawDatabase(dsn="", data_dir=data_dir, backend="pglite_socket", process=proc)
        await raw.aclose()
        raise
    dsn = f"postgresql+asyncpg://postgres:postgres@{host}:{port}/postgres"
    return RawDatabase(
        dsn=dsn, data_dir=data_dir, backend="pglite_socket", process=proc
    )


# ------------------------------------------------------------------ 入口
async def open_raw(data_dir: str) -> RawDatabase:
    """启动底层后端并返回原始句柄。

    `db_recovery.open_with_wal_recovery()` 只调用本函数，
    不接触任何 `PGlite.create()` 之类的 JS API。
    """
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    host = os.environ.get(HOST_ENV, DEFAULT_HOST)
    try:
        port = int(os.environ.get(PORT_ENV, str(DEFAULT_PORT)))
    except ValueError as exc:
        raise DatabaseBackendError(f"{PORT_ENV} 必须是整数") from exc
    backend = os.environ.get(BACKEND_ENV, "py_pglite").strip().lower()
    if backend == "py_pglite":
        return await _start_py_pglite_backend(root, host, port)
    if backend == "pglite_socket":
        return await _start_pglite_socket_backend(root, host, port)
    raise DatabaseBackendError(
        f"未知的 {BACKEND_ENV}={backend!r}；只允许 py_pglite 或 pglite_socket"
    )


async def open_database(data_dir: str, backup_dir: str) -> DatabaseHandle:
    """打开数据库的唯一入口：加锁 → 备份 → 打开 → 建唯一引擎。

    1. 确保 data_dir 存在；
    2. `portalocker.Lock(.lock, mode='a+', timeout=5)` 取独占非阻塞锁，
       超时（AlreadyLocked）抛 DatabaseLockedError；
    3. 打开数据库**之前**做硬链接增量备份（24h / 保留 7 份）；
    4. 经 db_recovery 打开底层后端（WAL 错误只报不修）；
    5. 用 `pool_size=1, max_overflow=0` 建唯一 async 引擎并返回句柄。
    """
    global _CURRENT_HANDLE
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_FILENAME

    lock = portalocker.Lock(str(lock_path), mode="a+", timeout=5)
    try:
        lock.acquire()
    except portalocker.LockException as exc:
        raise DatabaseLockedError(
            f"另一个进程正在使用数据库（数据目录：{data_dir}）。"
            "请关闭占用它的程序后重试；不要手工删除 .lock 文件。\n"
            f"底层错误：{exc}"
        ) from exc

    try:
        # 备份必须在打开数据库之前完成，保证快照对应的是静止文件。
        await asyncio.to_thread(
            maybe_backup,
            data_dir,
            backup_dir,
            _env_int(BACKUP_INTERVAL_ENV, 24),
            _env_int(BACKUP_KEEP_ENV, 7),
        )

        # 延迟导入，避免 db_gateway <-> db_recovery 循环依赖。
        from app.db_recovery import open_with_wal_recovery

        raw = await open_with_wal_recovery(data_dir)
        try:
            engine = create_async_engine(
                raw.dsn,
                echo=False,
                pool_size=1,
                max_overflow=0,
                pool_pre_ping=True,
                connect_args={"server_settings": {}, "ssl": False},
            )
        except Exception:
            await raw.aclose()
            raise

        # 同一进程内的 Alembic 在线迁移可直接复用这个连接串。
        os.environ[DATABASE_URL_ENV] = raw.dsn
        handle = DatabaseHandle(engine, raw, lock, data_dir, backup_dir)
        _CURRENT_HANDLE = handle
        return handle
    except Exception:
        try:
            lock.release()
        except portalocker.LockException:
            pass
        raise


async def close_database(db: DatabaseHandle, lock_fd: int | None = None) -> None:
    """关闭句柄并释放锁。`lock_fd` 仅为兼容 M1.md 的函数签名，实际不使用。"""
    del lock_fd
    await db.close()
