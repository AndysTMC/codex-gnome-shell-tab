"""Own desktop lifetime and input authorization for the tabs in one chat."""

import asyncio
import base64
import re
import time
import uuid

from controls import HEIGHT, WIDTH
from sandbox import DesktopError, InputError, MAX_JPEG_BYTES, Sandbox

VISIBLE_TAB_LEASE = 25
HIDDEN_TAB_LEASE = 150  # Allow a hidden Chromium tab to be throttled to one wakeup per minute.
INPUT_LEASE = 15
MAX_TABS = 3
TOKEN = re.compile(r"[0-9a-f]{32}", re.ASCII)
DISCONNECTED = "The GNOME Shell tab disconnected. Reconnect the tab to continue."


class DesktopTabs:
    def __init__(self):
        self.tabs = {}

    def require_tab(self, token):
        tab = self.tabs.get(token)
        if tab is None:
            raise ValueError(DISCONNECTED)
        return tab

    async def attach(self, token, visible, epoch):
        tab = self.tabs.get(token)
        if tab is not None and tab.expires <= tab.clock():
            await self.expire_tab(token, tab)
            tab = self.tabs.get(token)
        if tab is None:
            if len(self.tabs) >= MAX_TABS:
                raise ValueError(f"Close a GNOME Shell tab in this chat to open another (limit {MAX_TABS}).")
            tab = self.tabs[token] = Session()
        return await tab.attach(visible, epoch)

    async def view_action(self, token, action, epoch):
        if action == "close":
            tab = self.tabs.get(token)
            if tab is not None:
                await tab.close()
                if tab.box is not None:
                    raise ValueError(tab.problem)
                self.remove_tab(token, tab)
            return {}
        tab = self.require_tab(token)
        status = await tab.view_action(action, epoch)
        if action == "share":
            for other in self.tabs.values():
                if other is not tab:
                    other.grant = None
        return status

    def shared_tab(self):
        for tab in self.tabs.values():
            if tab.is_shared():
                return tab
        raise ValueError("Use Allow Codex in the live GNOME Shell tab before sharing the screen or input.")

    def remove_tab(self, token, tab):
        if self.tabs.get(token) is tab:
            del self.tabs[token]

    async def expire_tab(self, token, tab):
        if await tab.expire():
            self.remove_tab(token, tab)

    async def expire(self):
        await asyncio.gather(*(self.expire_tab(token, tab) for token, tab in self.tabs.items()))

    async def close(self):
        await asyncio.gather(*(tab.close() for tab in self.tabs.values()))


class Session:
    def __init__(self, *, make_sandbox=Sandbox, clock=time.monotonic):
        self.make_sandbox = make_sandbox
        self.clock = clock
        self.lock = asyncio.Lock()
        self.expires = clock() + VISIBLE_TAB_LEASE
        self.visible_until = 0
        self.epoch = -1
        self.closing = False
        self.cleanup_after = 0
        self.grant = None
        self.box = None
        self.monitor = None
        self.session_id = None
        self.phase = "stopped"
        self.problem = None
        self.warnings = []

    def status(self):
        return {
            "phase": self.phase, "shared": self.is_shared(), "epoch": self.epoch,
            "sessionId": self.session_id, "problem": self.problem,
            "width": WIDTH, "height": HEIGHT, "warnings": self.warnings,
        }

    def require_live(self):
        if self.closing or self.expires <= self.clock():
            raise ValueError(DISCONNECTED)

    def is_visible(self):
        return not self.closing and self.clock() < min(self.expires, self.visible_until)

    def is_shared(self):
        return self.grant is not None and self.phase == "running" and self.is_visible()

    async def expire(self):
        async with self.lock:
            now = self.clock()
            if self.closing or self.expires <= now:
                if now >= self.cleanup_after:
                    await self.close_locked()
                return self.box is None
            if self.visible_until and self.visible_until <= now:
                self.visible_until = 0
                await self.disarm_locked()
            return False

    async def attach(self, visible, epoch):
        async with self.lock:
            self.require_live()
            if self.visible_until:
                self.visible_until = 0
                await self.disarm_locked()
            await self.renew_locked(visible, epoch)
            return self.status()

    async def renew_locked(self, visible, epoch):
        now = self.clock()
        # A delayed frame must not make a tab visible again after it was hidden.
        if epoch < self.epoch:
            return
        self.expires = now + (VISIBLE_TAB_LEASE if visible else HIDDEN_TAB_LEASE)
        if self.visible_until and (self.visible_until <= now or epoch != self.epoch or not visible):
            self.visible_until = 0
            await self.disarm_locked()
        self.epoch = epoch
        if visible:
            self.visible_until = self.clock() + INPUT_LEASE
            if self.phase == "stopped":
                await self.start_locked()

    async def view_action(self, action, epoch):
        async with self.lock:
            self.require_live()
            match action:
                case "retry":
                    self.require_visible(epoch)
                    if self.phase == "error":
                        await self.stop_locked()
                        if self.box is None:
                            await self.start_locked()
                case "share":
                    self.require_visible(epoch)
                    if self.phase != "running":
                        raise ValueError("Wait for GNOME Shell to start before sharing.")
                    self.grant = object()
                case "unshare":
                    self.grant = None
                case _:
                    raise ValueError("Unknown session operation.")
            return self.status()

    async def disarm_locked(self):
        self.grant = None
        if self.phase == "running":
            try:
                await self.box.request({
                    "method": "input", "events": [{"type": "release"}], "agent": False, "smooth": False,
                }, guard=lambda: None)
            except (DesktopError, InputError) as error:
                await self.fail_locked(error)

    async def start_locked(self):
        if self.box is not None:
            return
        self.grant = None
        self.phase, self.problem, self.warnings = "starting", None, []
        self.session_id = uuid.uuid4().hex
        self.box = self.make_sandbox()
        try:
            await self.box.launch()
        except (OSError, DesktopError) as error:
            await self.fail_locked(error)
            return
        self.monitor = asyncio.create_task(self.watch(self.box))

    async def watch(self, box):
        try:
            warnings = await box.ready()
            async with self.lock:
                if self.box is not box:
                    return
                self.phase, self.warnings = "running", warnings
            await box.process.wait()
            raise DesktopError("The isolated GNOME session exited.")
        except DesktopError as error:
            async with self.lock:
                if self.box is box:
                    await self.fail_locked(error)

    async def stop_locked(self):
        box = self.box
        monitor, self.monitor = self.monitor, None
        self.phase, self.problem, self.warnings = "stopped", None, []
        self.grant, self.session_id = None, None
        if monitor is not None and monitor is not asyncio.current_task():
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if box is not None:
            try:
                await box.close()
                self.box = None
            except (OSError, TimeoutError) as error:
                box.log_failure(error)
                self.phase = "error"
                self.problem = "Could not confirm desktop shutdown. See the MCP server log."
                self.box = box

    async def fail_locked(self, error):
        box = self.box
        await self.stop_locked()
        if self.phase != "error":
            self.phase, self.problem = "error", str(error)
        if box is not None:
            box.log_failure(error)

    async def close(self):
        async with self.lock:
            await self.close_locked()

    async def close_locked(self):
        self.closing = True
        self.visible_until = 0
        await self.stop_locked()
        self.cleanup_after = self.clock() + 5

    def require_shared(self, grant):
        if not self.is_shared() or self.grant is not grant:
            raise ValueError("Use Allow Codex in the live GNOME Shell tab before sharing the screen or input.")

    def require_session(self, session_id):
        if self.phase != "running" or self.session_id != session_id:
            raise ValueError("The desktop session changed. Get the current screen and try again.")

    async def request(self, box, message, guard):
        try:
            result = await box.request(message, guard=guard)
            if message["method"] == "frame":
                return frame_payload(result, message["after"])
            return result
        except DesktopError as error:
            async with self.lock:
                if box is self.box:
                    await self.fail_locked(error)
            raise

    def require_visible(self, epoch):
        self.require_live()
        if epoch != self.epoch or not self.is_visible():
            raise ValueError("Show the GNOME Shell tab before using the desktop.")

    async def ui_frame(self, visible, epoch, session_id, since):
        async with self.lock:
            self.require_live()
            await self.renew_locked(visible, epoch)
            box, current = self.box, self.session_id
            if not visible or epoch != self.epoch or self.phase != "running":
                return self.status()
        # A fresh or reconnected view gets the current frame even if its sequence was reset.
        after = since if current == session_id else 0

        def guard():
            self.require_visible(epoch)
            self.require_session(current)

        try:
            result = await self.request(box, {"method": "frame", "after": after}, guard)
        except DesktopError:
            # The worker failure was handled above. Report the stopped or failed state to the view.
            self.require_live()
            return self.status()
        guard()
        return {**self.status(), **result}

    async def screenshot(self):
        grant = self.grant
        self.require_shared(grant)
        box, current = self.box, self.session_id

        def guard():
            self.require_shared(grant)
            self.require_session(current)

        result = await self.request(box, {"method": "frame", "after": 0}, guard)
        guard()
        return current, result["frame"]

    async def send_input(self, epoch, session_id, events, *, smooth=False):
        grant = self.grant

        def guard():
            if epoch is None:
                self.require_shared(grant)
            else:
                self.require_visible(epoch)
            self.require_session(session_id)

        guard()
        await self.request(self.box, {
            "method": "input", "events": events, "agent": epoch is None, "smooth": smooth,
        }, guard)


def frame_payload(result, after):
    sequence = result.get("seq")
    data = result.get("frame")
    if type(sequence) is not int or not 1 <= sequence < 2**31:
        raise DesktopError("GNOME returned an invalid frame sequence.")
    if sequence <= after and data is None:
        return {"seq": sequence}
    if sequence <= after or not isinstance(data, str) or len(data) > (MAX_JPEG_BYTES // 3 + 1) * 4:
        raise DesktopError("GNOME returned an invalid frame.")
    try:
        jpeg = base64.b64decode(data, validate=True)
    except ValueError as error:
        raise DesktopError("GNOME returned an invalid frame.") from error
    if len(jpeg) > MAX_JPEG_BYTES or not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
        raise DesktopError("GNOME returned an invalid frame.")
    return {"seq": sequence, "frame": data}
