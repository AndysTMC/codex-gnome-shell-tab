"""Launch and supervise a single isolated GNOME desktop."""

import asyncio
import json
import os
from pathlib import Path
import signal
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parent
MAX_JPEG_BYTES = 768 * 1024
MAX_REPLY_BYTES = (MAX_JPEG_BYTES // 3 + 1) * 4 + 8192
GUEST = "/session"
GUEST_HOME = "/home/gnome"
DEPENDENCIES = (
    "/usr/bin/bwrap", "/usr/bin/systemd-run", "/usr/bin/systemctl",
    "/usr/bin/dbus-daemon", "/usr/bin/gnome-shell", "/usr/bin/pipewire",
    "/usr/bin/wireplumber",
)


class DesktopError(RuntimeError):
    """The desktop transport or its isolated worker failed."""


class InputError(ValueError):
    """The worker rejected input before applying it."""


def extension_store():
    data = os.environ.get("XDG_DATA_HOME", "")
    base = Path(data) if Path(data).is_absolute() else Path.home() / ".local/share"
    return base / "gnome-shell-tab/extensions"


def scope_environment():
    # The Codex stdio launcher does not inherit XDG_RUNTIME_DIR or the session bus.
    # Only the trusted scope launcher uses the current Linux user's systemd directory.
    uid = os.getuid()
    runtime = Path("/run/user") / str(uid)
    try:
        info = runtime.lstat()
    except FileNotFoundError as error:
        raise DesktopError("Start Codex under an active Linux systemd user session.") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise DesktopError("The systemd user runtime directory is not owned and secured by this user.")
    return {"XDG_RUNTIME_DIR": str(runtime), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def command(unit, extensions):
    arguments = [
        "/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}",
        "-p", "MemoryMax=1536M", "-p", "TasksMax=384", "-p", "CPUQuota=200%",
        "-p", "OOMPolicy=kill", "--",
        "/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent",
        "--new-session", "--clearenv", "--cap-drop", "ALL", "--uid", "1000", "--gid", "1000",
        "--ro-bind", "/usr", "/usr", "--tmpfs", "/usr/local",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
        "--proc", "/proc", "--dev", "/dev", "--size", str(64 * 1024**2), "--tmpfs", "/dev/shm",
        "--dir", "/sys", "--dir", "/run", "--dir", "/var", "--dir", "/etc",
        "--ro-bind-try", "/etc/fonts", "/etc/fonts",
        "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--size", str(512 * 1024**2), "--tmpfs", GUEST,
        "--symlink", f"{GUEST}/home", "/home", "--symlink", f"{GUEST}/tmp", "/tmp",
        "--perms", "0700", "--dir", f"{GUEST}{GUEST_HOME}",
        "--perms", "01777", "--dir", f"{GUEST}/tmp",
        "--chdir", GUEST_HOME,
    ]
    environment = {
        "HOME": GUEST_HOME, "USER": "gnome", "LOGNAME": "gnome", "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "wayland",
        "XDG_RUNTIME_DIR": f"{GUEST}/run", "XDG_DATA_HOME": f"{GUEST}/data",
        "XDG_CONFIG_HOME": f"{GUEST}/config", "XDG_CACHE_HOME": f"{GUEST}/cache",
        "XDG_STATE_HOME": f"{GUEST}/state",
        "WAYLAND_DISPLAY": "wayland-0", "GDK_BACKEND": "wayland", "XKB_DEFAULT_LAYOUT": "us",
        "LIBGL_ALWAYS_SOFTWARE": "1", "LP_NUM_THREADS": "2",
        "GSETTINGS_BACKEND": "keyfile", "GIO_USE_VFS": "local", "TZ": "Etc/UTC", "GVFS_DISABLE_FUSE": "1", "NO_AT_BRIDGE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key, value in environment.items():
        arguments += ["--setenv", key, value]
    for name in ("desktop.py", "controls.py"):
        arguments += ["--ro-bind", str(ROOT / name), f"/bridge/{name}"]
    if extensions.is_dir():
        if extensions.is_symlink():
            raise DesktopError("Use a directory for the optional extension store, then start a new session.")
        arguments += ["--ro-bind", str(extensions), "/extensions"]
    return arguments + ["--", "/usr/bin/python3", "/bridge/desktop.py"]


class Sandbox:
    def __init__(self):
        self.unit = f"codex-gnome-shell-{uuid.uuid4().hex}.scope"
        self.process = None
        self.stderr_task = None
        self.errors = bytearray()
        self.io_lock = asyncio.Lock()
        self.closing = False

    async def launch(self):
        missing = [Path(path).name for path in DEPENDENCIES if not os.access(path, os.X_OK)]
        if missing:
            raise DesktopError("Missing local desktop dependencies: " + ", ".join(missing))
        self.scope_env = scope_environment()
        self.process = await asyncio.create_subprocess_exec(
            *command(self.unit, extension_store()), env=self.scope_env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_REPLY_BYTES,
            start_new_session=True,
        )
        self.stderr_task = asyncio.create_task(self.collect_stderr())

    async def collect_stderr(self):
        while chunk := await self.process.stderr.read(2048):
            self.errors.extend(chunk)
            del self.errors[:-8192]

    def diagnostic(self):
        return self.errors.decode("utf-8", errors="replace").strip()

    async def read(self, timeout):
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
            if not line or not line.endswith(b"\n"):
                raise DesktopError("The isolated desktop closed its control connection.")
            result = json.loads(line)
        except TimeoutError as error:
            raise DesktopError("The isolated desktop stopped responding.") from error
        except (ValueError, UnicodeError, OSError) as error:
            raise DesktopError("The isolated desktop returned an invalid control message.") from error
        if not isinstance(result, dict):
            raise DesktopError("The isolated desktop returned an invalid control message.")
        return result

    async def ready(self):
        result = await self.read(30)
        if result.get("ok") is not True:
            raise DesktopError("GNOME Shell could not start inside the isolated desktop.")
        warnings = result.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
            raise DesktopError("The isolated desktop returned an invalid startup status.")
        return [item[:160] for item in warnings[:8]]

    async def request(self, message, *, guard):
        async with self.io_lock:
            guard()
            if self.closing or self.process.returncode is not None:
                raise DesktopError("The isolated desktop is no longer running.")
            try:
                self.process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
                await asyncio.wait_for(self.process.stdin.drain(), 1)
            except (TimeoutError, OSError) as error:
                raise DesktopError("The isolated desktop stopped accepting requests.") from error
            result = await self.read(3)
            if result.get("input_error") == "keymap":
                raise InputError("GNOME rejected a key that is unavailable in the guest’s US keyboard layout.")
            if result.get("input_error") == "held":
                raise InputError("The desktop has held user input. Wait for the user to release it.")
            if result.get("ok") is not True:
                raise DesktopError("GNOME could not complete the desktop operation.")
            return result

    async def close(self):
        if self.process is None:
            return
        self.closing = True
        process = self.process
        process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                await self.kill_scope()
                # Also cover a scope launch that timed out before its unit was created.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(process.wait(), 3)
        if self.stderr_task:
            self.stderr_task.cancel()
            await asyncio.gather(self.stderr_task, return_exceptions=True)
        self.process = None

    async def kill_scope(self):
        killer = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl", "--user", "kill", "--kill-whom=all", "--signal=KILL", self.unit,
            env=self.scope_env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), 2)
        except TimeoutError:
            killer.kill()
            await killer.wait()

    def log_failure(self, error):
        print(f"GNOME Shell: {error}", file=sys.stderr, flush=True)
        if self.diagnostic():
            print(self.diagnostic(), file=sys.stderr, flush=True)
