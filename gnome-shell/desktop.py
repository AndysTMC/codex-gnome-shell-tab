"""Capture and operate Mutter inside the isolated desktop."""

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from controls import BUTTON_CODES, HEIGHT, WIDTH

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst

REMOTE = "org.gnome.Mutter.RemoteDesktop"
CAST = "org.gnome.Mutter.ScreenCast"
DISPLAY = "org.gnome.Mutter.DisplayConfig"
RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"])
LOG = RUNTIME / "desktop.log"
MAX_JPEG_BYTES = 768 * 1024
XKB = ctypes.CDLL("libxkbcommon.so.0")
XKB.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
XKB.xkb_keysym_from_name.restype = ctypes.c_uint32
XKB.xkb_utf32_to_keysym.argtypes = [ctypes.c_uint32]
XKB.xkb_utf32_to_keysym.restype = ctypes.c_uint32
u32, pointer = ctypes.c_uint32, ctypes.c_void_p
symbol_array = ctypes.POINTER(u32)
for function, args, returns in (
    (XKB.xkb_context_new, [ctypes.c_int], pointer),
    (XKB.xkb_context_unref, [pointer], None),
    (XKB.xkb_keymap_new_from_names, [pointer, pointer, ctypes.c_int], pointer),
    (XKB.xkb_keymap_unref, [pointer], None),
    (XKB.xkb_keymap_min_keycode, [pointer], u32),
    (XKB.xkb_keymap_max_keycode, [pointer], u32),
    (XKB.xkb_keymap_num_levels_for_key, [pointer, u32, u32], u32),
    (XKB.xkb_keymap_key_get_syms_by_level, [pointer, u32, u32, u32, ctypes.POINTER(symbol_array)], ctypes.c_int),
):
    function.argtypes, function.restype = args, returns

# GNOME requires a system bus connection. This separate bus has no services or activation paths.
SYSTEM_BUS = """<busconfig>
  <type>system</type>
  <auth>EXTERNAL</auth>
  <listen>unix:path=/session/run/system-bus</listen>
  <policy context="default">
    <allow user="gnome"/>
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


class RejectedInput(ValueError):
    pass


def call(bus, interface, method, signature="()", args=(), *, dest=None, path=None, timeout=2500):
    dest = dest or interface
    path = path or "/" + dest.replace(".", "/")
    return bus.call_sync(
        dest, path, interface, method, GLib.Variant(signature, args), None,
        Gio.DBusCallFlags.NO_AUTO_START, timeout, None,
    ).unpack()


def wait_for(check, detail, timeout):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise RuntimeError(detail)


def bootstrap():
    os.umask(0o077)
    for variable in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        Path(os.environ[variable]).mkdir(mode=0o700)
    Path("/etc/passwd").write_text("gnome:x:1000:1000:GNOME:/home/gnome:/bin/bash\n")
    Path("/etc/group").write_text("gnome:x:1000:\n")
    Path("/etc/machine-id").write_text(uuid.uuid4().hex + "\n")
    Path("/etc/guest-system-bus.conf").write_text(SYSTEM_BUS)
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={RUNTIME}/session-bus"
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={RUNTIME}/system-bus"


def configure_extensions():
    source = Path("/extensions")
    if not source.is_dir():
        return []
    target = Path(os.environ["XDG_DATA_HOME"]) / "gnome-shell/extensions"
    target.mkdir(parents=True)
    uuids, warnings = [], []
    for entry in sorted(source.iterdir()):
        if not re.fullmatch(r"[\w.+-]+@[\w.+-]+", entry.name, flags=re.ASCII):
            continue
        try:
            metadata = entry / "metadata.json"
            if entry.is_symlink() or metadata.is_symlink() or not metadata.is_file():
                raise ValueError
            with metadata.open("rb") as stream:
                data = json.loads(stream.read(65537))
            if not isinstance(data, dict) or data.get("uuid") != entry.name:
                raise ValueError
            (target / entry.name).symlink_to(entry, target_is_directory=True)
        except (OSError, ValueError):
            warnings.append(f"Skipped {entry.name[:88]} because its metadata could not be validated.")
        else:
            uuids.append(entry.name)
    if uuids:
        settings = Gio.Settings.new("org.gnome.shell")
        settings.set_boolean("disable-user-extensions", False)
        settings.set_strv("enabled-extensions", uuids)
        Gio.Settings.sync()
    return warnings


def keyboard_symbols():
    # Mutter acknowledges a keysym before checking whether the layout can produce it.
    # Match the guest's default layout so unrepresentable keys fail before a batch starts.
    context = XKB.xkb_context_new(0)
    if not context:
        raise RuntimeError("Could not initialize the guest keyboard.")
    try:
        keymap = XKB.xkb_keymap_new_from_names(context, None, 0)
        if not keymap:
            raise RuntimeError("The guest's XKB keyboard data is missing.")
        try:
            supported = set()
            start = XKB.xkb_keymap_min_keycode(keymap)
            end = XKB.xkb_keymap_max_keycode(keymap)
            for keycode in range(start, end):
                levels = XKB.xkb_keymap_num_levels_for_key(keymap, keycode, 0)
                for level in range(levels):
                    symbols = symbol_array()
                    count = XKB.xkb_keymap_key_get_syms_by_level(keymap, keycode, 0, level, ctypes.byref(symbols))
                    for index in range(count):
                        supported.add(symbols[index])
            return supported
        finally:
            XKB.xkb_keymap_unref(keymap)
    finally:
        XKB.xkb_context_unref(context)


def keysym(name, supported):
    if len(name) == 1:
        value = XKB.xkb_utf32_to_keysym(ord(name))
    else:
        value = XKB.xkb_keysym_from_name(name.encode("ascii"), 0)
    if not value or value not in supported:
        raise RejectedInput("keymap")
    return value


class Desktop:
    def __init__(self):
        self.keys = set()
        self.buttons = set()
        self.processes = []
        self.pipeline = None
        self.jpeg = None
        self.encoded = None
        self.sequence = 0

    def spawn(self, command, log):
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.processes.append(proc)
        return proc

    def check_processes(self):
        for proc in self.processes:
            if proc.poll() is not None:
                raise RuntimeError(f"{Path(proc.args[0]).name} exited ({proc.returncode}).")

    def socket_exists(self, name):
        self.check_processes()
        return (RUNTIME / name).is_socket()

    def start(self):
        bootstrap()
        version = subprocess.check_output(["/usr/bin/gnome-shell", "--version"], text=True, timeout=3)
        if not re.match(r"GNOME Shell 50(?:\.|\s|$)", version):
            raise RuntimeError("This plugin supports GNOME Shell 50 and its Mutter remote desktop interfaces.")
        settings = Gio.Settings.new("org.gnome.desktop.input-sources")
        settings.set_value("sources", GLib.Variant("a(ss)", [("xkb", "us")]))
        background = Gio.Settings.new("org.gnome.desktop.background")
        background.set_string("picture-options", "none")
        background.set_string("color-shading-type", "solid")
        background.set_string("primary-color", "#151618")
        Gio.Settings.sync()
        self.symbols = keyboard_symbols()
        warnings = configure_extensions()
        with LOG.open("wb") as log:
            self.spawn(["/usr/bin/dbus-daemon", "--session", "--nofork", "--nopidfile",
                        f"--address={os.environ['DBUS_SESSION_BUS_ADDRESS']}"], log)
            self.spawn(["/usr/bin/dbus-daemon", "--config-file=/etc/guest-system-bus.conf",
                        "--nofork", "--nopidfile"], log)
            wait_for(lambda: self.socket_exists("session-bus") and self.socket_exists("system-bus"),
                     "The private D-Bus channels did not start.", 4)
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            self.spawn(["/usr/bin/pipewire"], log)
            wait_for(lambda: self.socket_exists("pipewire-0"), "The private PipeWire server did not start.", 5)
            self.spawn(["/usr/bin/wireplumber"], log)
            self.spawn(["/usr/bin/gnome-shell", "--headless", "--wayland", "--no-x11",
                        f"--virtual-monitor={WIDTH}x{HEIGHT}@30"], log)

        # Wait for Shell's application name, which is registered after the JS UI starts.
        def compositor_ready():
            self.check_processes()
            names = ("org.gnome.Shell", REMOTE, CAST, DISPLAY)
            return all(call(self.bus, "org.freedesktop.DBus", "NameHasOwner", "(s)", (name,))[0]
                       for name in names)

        wait_for(compositor_ready, "Headless GNOME Shell did not initialize.", 15)
        monitors = call(self.bus, DISPLAY, "GetCurrentState")[1]
        if len(monitors) != 1:
            raise RuntimeError("Expected exactly one private monitor.")
        devices, = call(self.bus, "org.freedesktop.DBus.Properties", "Get", "(ss)",
                        (REMOTE, "SupportedDeviceTypes"), dest=REMOTE)
        if devices & 3 != 3:
            raise RuntimeError("Mutter did not advertise keyboard and pointer support.")
        self.remote_path, = call(self.bus, REMOTE, "CreateSession")
        session_id, = call(
            self.bus, "org.freedesktop.DBus.Properties", "Get", "(ss)",
            (REMOTE + ".Session", "SessionId"), dest=REMOTE, path=self.remote_path,
        )
        cast_path, = call(self.bus, CAST, "CreateSession", "(a{sv})", ({
            "remote-desktop-session-id": GLib.Variant("s", session_id),
        },))
        self.stream_path, = call(
            self.bus, CAST + ".Session", "RecordMonitor", "(sa{sv})",
            (monitors[0][0][0], {"cursor-mode": GLib.Variant("u", 1)}), dest=CAST, path=cast_path,
        )
        nodes = []
        subscription = self.bus.signal_subscribe(
            CAST, CAST + ".Stream", "PipeWireStreamAdded", self.stream_path, None,
            Gio.DBusSignalFlags.NONE, lambda *event: nodes.append(event[5].unpack()[0]),
        )
        try:
            self.notify("Start", timeout=5000)
            node = wait_for(lambda: nodes and nodes[0], "Mutter did not start the screen stream.", 4)
        finally:
            self.bus.signal_unsubscribe(subscription)

        Gst.init(None)
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc path={node} ! videorate drop-only=true ! "
            f"video/x-raw,width={WIDTH},height={HEIGHT},framerate=8/1 ! "
            "videoconvert ! jpegenc quality=74 ! appsink name=frames max-buffers=1 drop=true sync=false"
        )
        self.sink = self.pipeline.get_by_name("frames")
        self.pipeline.set_state(Gst.State.PLAYING)
        wait_for(self.capture, "The screen stream produced no frames.", 6)
        return warnings

    def capture(self):
        self.check_processes()
        message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message:
            if message.type == Gst.MessageType.EOS:
                raise RuntimeError("The GNOME screen stream ended.")
            cause, _ = message.parse_error()
            raise RuntimeError(f"Screen capture failed: {cause.message}")
        sample = self.sink.emit("try-pull-sample", 0)
        if sample:
            buffer = sample.get_buffer()
            length = buffer.get_size()
            if not 4 <= length <= MAX_JPEG_BYTES:
                raise RuntimeError("Screen capture returned an invalid JPEG size.")
            jpeg = buffer.extract_dup(0, length)
            if jpeg != self.jpeg:
                self.sequence += 1
                self.jpeg = jpeg
                self.encoded = None
        return self.jpeg is not None

    def frame(self, after):
        self.capture()
        response = {"seq": self.sequence}
        if self.sequence > after:
            if self.encoded is None:
                self.encoded = base64.b64encode(self.jpeg).decode("ascii")
            response["frame"] = self.encoded
        return response

    def notify(self, method, signature="()", args=(), *, timeout=800):
        return call(self.bus, REMOTE + ".Session", method, signature, args,
                    dest=REMOTE, path=self.remote_path, timeout=timeout)

    def change(self, held, code, pressed, method, signature):
        if (code in held) == pressed:
            return
        self.notify(method, signature, (code, pressed))
        (held.add if pressed else held.discard)(code)

    def release(self):
        for code in self.keys.copy():
            self.change(self.keys, code, False, "NotifyKeyboardKeysym", "(ub)")
        for code in self.buttons.copy():
            self.change(self.buttons, code, False, "NotifyPointerButton", "(ib)")

    def input(self, events, *, agent, smooth):
        self.check_processes()
        if agent and (self.keys or self.buttons):
            raise RejectedInput("held")
        # Resolve the whole batch before changing the pressed state.
        resolved = {e["key"]: keysym(e["key"], self.symbols) for e in events if e["type"] == "key"}
        for event in events:
            match event["type"]:
                case "move":
                    self.notify("NotifyPointerMotionAbsolute", "(sdd)",
                                (self.stream_path, event["x"], event["y"]))
                    if smooth:
                        time.sleep(0.022)
                case "button":
                    self.change(self.buttons, BUTTON_CODES[event["button"]], event["pressed"],
                                "NotifyPointerButton", "(ib)")
                case "key":
                    self.change(self.keys, resolved[event["key"]], event["pressed"],
                                "NotifyKeyboardKeysym", "(ub)")
                case "scroll":
                    self.notify("NotifyPointerAxisDiscrete", "(ui)", (event["axis"], event["steps"]))
                case "release":
                    self.release()
        if agent:
            # Aliased keysyms in a chord still leave no held input.
            self.release()

    def close(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        # Bubblewrap's PID namespace also reaps any D-Bus-activated descendants.
        for proc in reversed(self.processes):
            if proc.poll() is None:
                proc.terminate()


def respond(**fields):
    print(json.dumps(fields, separators=(",", ":")), flush=True)


def main():
    def stop(_signum, _frame):
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    desktop = Desktop()
    try:
        warnings = desktop.start()
        respond(ok=True, warnings=warnings)
        for line in sys.stdin.buffer:
            request = json.loads(line)
            match request["method"]:
                case "frame":
                    respond(ok=True, **desktop.frame(request["after"]))
                case "input":
                    try:
                        desktop.input(request["events"], agent=request["agent"], smooth=request["smooth"])
                    except RejectedInput as error:
                        respond(input_error=str(error))
                    else:
                        respond(ok=True)
    except (OSError, RuntimeError, GLib.Error, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr, flush=True)
        if LOG.is_file():
            with LOG.open("rb") as log:
                log.seek(max(0, LOG.stat().st_size - 4096))
                print(log.read().decode("utf-8", errors="replace"), file=sys.stderr, flush=True)
        respond(ok=False)
    finally:
        desktop.close()


if __name__ == "__main__":
    main()
