import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gnome-shell"))
from controls import action_events, validate_events
from sandbox import DesktopError
from session import DesktopTabs, INPUT_LEASE, MAX_TABS, VISIBLE_TAB_LEASE, Session, frame_payload

JPEG = "/9j/2Q=="
VIEW = "a" * 32
NEW_VIEW = "b" * 32


class FakeSandbox:
    def __init__(self):
        self.exited = asyncio.Event()
        self.process = self
        self.closed = 0
        self.requests = []
        self.guards = asyncio.Lock()
        self.invalid_frame = False
        self.wait_until_frame = None
        self.errors = []

    async def launch(self):
        pass

    async def ready(self):
        return []

    async def wait(self):
        await self.exited.wait()

    async def request(self, message, *, guard):
        async with self.guards:
            guard()
            self.requests.append(message)
            if message["method"] == "frame":
                if self.wait_until_frame:
                    await self.wait_until_frame.wait()
                return {"frame": "garbage" if self.invalid_frame else JPEG, "seq": 1}
            return {"ok": True}

    async def close(self):
        self.closed += 1
        self.exited.set()

    def log_failure(self, error):
        self.errors.append(error)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.tabs = DesktopTabs()
        sessions = patch("session.Session", side_effect=lambda: Session(
            make_sandbox=FakeSandbox, clock=lambda: self.now,
        ))
        sessions.start()
        self.addCleanup(sessions.stop)

    async def asyncTearDown(self):
        await self.tabs.close()

    async def running(self, token=VIEW):
        self.assertEqual((await self.tabs.attach(token, True, 1))["phase"], "starting")
        tab = self.tabs.require_tab(token)
        await asyncio.sleep(0)
        self.assertEqual(tab.phase, "running")
        return tab

    async def test_visible_tab_auto_starts_once(self):
        self.assertEqual((await self.tabs.attach(VIEW, False, 0))["phase"], "stopped")
        tab = self.tabs.require_tab(VIEW)
        self.assertIsNone(tab.box)
        self.assertEqual((await tab.ui_frame(True, 1, None, 0))["phase"], "starting")
        await asyncio.sleep(0)
        original = tab.session_id
        self.assertEqual((await self.tabs.attach(VIEW, True, 1))["sessionId"], original)

    async def test_input_lease_expires_before_orphan_cleanup(self):
        tab = await self.running()
        box = tab.box
        await self.tabs.view_action(VIEW, "share", 1)
        self.now += INPUT_LEASE + 0.1
        with self.assertRaisesRegex(ValueError, "Allow Codex"):
            await tab.screenshot()
        await self.tabs.expire()
        self.assertEqual(box.requests[-1]["events"], [{"type": "release"}])
        self.assertEqual(box.closed, 0)
        self.now += VISIBLE_TAB_LEASE - INPUT_LEASE
        await self.tabs.expire()
        self.assertEqual(box.closed, 1)
        self.assertEqual(len(self.tabs.tabs), 0)

    async def test_hidden_tab_survives_throttled_background_heartbeats(self):
        tab = await self.running()
        original = tab.session_id
        await self.tabs.view_action(VIEW, "share", 1)
        hidden = await tab.ui_frame(False, 2, original, 0)
        self.assertFalse(hidden["shared"])
        self.assertNotIn("frame", hidden)
        for _ in range(4):
            self.now += 65
            await self.tabs.expire()
            self.assertNotIn("frame", await tab.ui_frame(False, 2, original, 0))
        state = await tab.ui_frame(True, 3, original, 0)
        self.assertEqual(state["sessionId"], original)
        self.assertFalse(state["shared"])
        self.assertIn("frame", state)

    async def test_out_of_order_presence_and_access(self):
        tab = await self.running()
        await self.tabs.view_action(VIEW, "share", 1)
        await tab.ui_frame(True, 3, tab.session_id, 0)
        self.assertFalse(tab.is_shared())
        self.assertEqual(tab.box.requests[-2]["events"], [{"type": "release"}])
        await tab.ui_frame(False, 2, tab.session_id, 0)
        await tab.ui_frame(True, 1, tab.session_id, 0)
        self.assertTrue(tab.is_visible())
        with self.assertRaisesRegex(ValueError, "Show"):
            await self.tabs.view_action(VIEW, "share", 1)
        with self.assertRaisesRegex(ValueError, "Show"):
            await tab.send_input(1, tab.session_id, [{"type": "release"}])
        await tab.ui_frame(False, 4, tab.session_id, 0)
        await tab.ui_frame(True, 3, tab.session_id, 0)
        self.assertFalse(tab.is_visible())

    async def test_independent_tabs_and_current_shared_target(self):
        first, second = await self.running(), await self.running(NEW_VIEW)
        original, other = first.session_id, second.session_id
        self.assertNotEqual(original, other)
        await self.tabs.view_action(VIEW, "share", 1)
        self.assertEqual((await self.tabs.shared_tab().screenshot())[0], original)
        await self.tabs.view_action(NEW_VIEW, "share", 1)
        self.assertEqual((await self.tabs.shared_tab().screenshot())[0], other)
        with self.assertRaisesRegex(ValueError, "desktop session changed"):
            await self.tabs.shared_tab().send_input(None, original, [{"type": "release"}])
        await self.tabs.view_action(NEW_VIEW, "close", 1)
        await self.tabs.view_action(NEW_VIEW, "close", 1)
        self.assertEqual(first.session_id, original)
        self.assertIsNone(second.box)
        with self.assertRaisesRegex(ValueError, "Allow Codex"):
            self.tabs.shared_tab()
        self.assertNotEqual((await self.running(NEW_VIEW)).session_id, other)

    async def test_tab_limit_and_space_after_close(self):
        keys = [str(i) * 32 for i in range(MAX_TABS + 1)]
        for key in keys[:-1]:
            await self.running(key)
        with self.assertRaisesRegex(ValueError, "Close a GNOME Shell tab"):
            await self.tabs.attach(keys[-1], True, 1)
        await self.tabs.view_action(keys[0], "close", 1)
        await self.running(keys[-1])

    async def test_revocation_and_regrant_while_a_screenshot_is_in_flight(self):
        tab = await self.running()
        await self.tabs.view_action(VIEW, "share", 1)
        tab.box.wait_until_frame = asyncio.Event()
        screenshot = asyncio.create_task(tab.screenshot())
        await asyncio.sleep(0)
        await self.tabs.view_action(VIEW, "unshare", 1)
        await self.tabs.view_action(VIEW, "share", 1)
        tab.box.wait_until_frame.set()
        with self.assertRaisesRegex(ValueError, "Allow Codex"):
            await screenshot

    async def test_revocation_while_action_waits_for_worker(self):
        tab = await self.running()
        await self.tabs.view_action(VIEW, "share", 1)
        async with tab.box.guards:
            act = asyncio.create_task(tab.send_input(None, tab.session_id, [{"type": "release"}]))
            await asyncio.sleep(0)
            await tab.view_action("unshare", 1)
        with self.assertRaisesRegex(ValueError, "Allow Codex"):
            await act
        self.assertFalse(tab.box.requests)

    async def test_reconnecting_same_view_revokes_existing_sharing(self):
        await self.running()
        await self.tabs.view_action(VIEW, "share", 1)
        self.assertFalse((await self.tabs.attach(VIEW, True, 1))["shared"])

    async def test_invalid_worker_frame_closes_desktop_and_allows_retry(self):
        tab = await self.running()
        box, original = tab.box, tab.session_id
        box.invalid_frame = True
        report = await tab.ui_frame(True, 1, None, 0)
        self.assertEqual(report["phase"], "error")
        self.assertIn("invalid frame", report["problem"])
        self.assertNotIn("frame", report)
        self.assertEqual(box.closed, 1)
        self.assertEqual((await tab.ui_frame(True, 1, None, 0))["phase"], "error")
        self.assertEqual((await tab.view_action("retry", 1))["phase"], "starting")
        self.assertNotEqual(tab.session_id, original)

    async def test_cancelled_close_keeps_worker_for_cleanup_retry(self):
        tab = await self.running()
        box = tab.box
        old_close = box.close
        close_entered = asyncio.Event()

        async def stalled_close():
            close_entered.set()
            await asyncio.Future()

        box.close = stalled_close
        task = asyncio.create_task(self.tabs.view_action(VIEW, "close", 1))
        await close_entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        box.close = old_close
        await self.tabs.expire()
        self.assertEqual(box.closed, 1)
        self.assertEqual(len(self.tabs.tabs), 0)


class InputTests(unittest.TestCase):
    def test_rejects_bounds_nonfinite_coordinates_and_bad_keys(self):
        for x in (-0.1, 1024, True, float("inf"), float("nan")):
            with self.subTest(x=x), self.assertRaises(ValueError):
                validate_events([{"type": "move", "x": x, "y": 0}])
        for key in ("\x00", "\ud800", "a b", {}, "A" * 33):
            with self.subTest(key=key), self.assertRaises(ValueError):
                action_events({"type": "press_keys", "keys":[key]})

    def test_chord_and_drag_include_releases(self):
        sequence = action_events({"type": "press_keys", "keys": ["Control_L", "a"]})
        self.assertEqual([event["key"] for event in sequence], ["Control_L", "a", "a", "Control_L"])
        self.assertEqual([event["pressed"] for event in sequence], [True, True, False, False])
        sequence = action_events({"type":"drag", "x": 8,"y": 17,"to_x":50,"to_y":29})
        self.assertEqual(sequence[-2], {"type": "move", "x": 50, "y": 29})
        self.assertEqual(sequence[-1], {"type": "button", "button": 0, "pressed": False})

    def test_frame_shape_validation(self):
        self.assertEqual(frame_payload({"frame": JPEG, "seq": 1}, 0), {"frame": JPEG, "seq": 1})
        self.assertEqual(frame_payload({"seq": 1}, 1), {"seq": 1})
        for seq, data in ((True, JPEG), (1, "a"), (1, "abcd"), (0, JPEG), (1, None)):
            with self.subTest(seq=seq, data=data), self.assertRaises(DesktopError):
                frame_payload({"seq": seq, "frame": data}, 0)


if __name__ == "__main__":
    unittest.main()
