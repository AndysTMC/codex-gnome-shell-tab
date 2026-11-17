"""GNOME integration tests. Run with system Python on the GNOME host."""

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "gnome-shell/server.py"
SYSTEMCTL = "/usr/bin/systemctl"
TESSERACT = shutil.which("tesseract")
SCOPE = re.compile(r"codex-gnome-shell-[0-9a-f]{32}\.scope")


class Client:
    def __init__(self, data_home):
        self.proc = None
        self.data_home = data_home
        self.counter = 0
        self.view = uuid.uuid4().hex
        self.session = None
        self.after = 0
        self.epoch = 1
        self.visible = True
        self.tool_schema = None

    async def __aenter__(self):
        mcp_env = {name: value for name, value in os.environ.items()
                   if name not in {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}}
        self.proc = await asyncio.create_subprocess_exec(
            "/usr/bin/python3", "-EsB", str(SERVER),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            env={**mcp_env, "XDG_DATA_HOME": str(self.data_home), "GNOME_AUDIT_SECRET": "fixture-only"},
            limit=2 * 1024**2,
        )
        result = await self.call("initialize", protocolVersion="2025-11-25")
        assert result["protocolVersion"] == "2025-11-25"
        assert "io.modelcontextprotocol/ui" in result["capabilities"]["extensions"]
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tool_schema = (await self.call("tools/list"))["tools"]
        return self

    async def __aexit__(self, *_):
        if self.proc.returncode is None:
            self.proc.stdin.close()
        await asyncio.wait_for(self.proc.wait(), 9)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")

    async def read(self):
        line = await asyncio.wait_for(self.proc.stdout.readline(), 15)
        assert line, f"MCP server exited with {self.proc.returncode}"
        msg = json.loads(line)
        assert msg["jsonrpc"] == "2.0"
        return msg

    async def call(self, method, expected_error=None, **params):
        self.counter += 1
        identifier = self.counter
        self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        msg = await self.read()
        assert msg["id"] == identifier, msg
        if expected_error is not None:
            assert msg["error"]["code"] == expected_error, msg
            return msg["error"]
        assert "error" not in msg, msg
        return msg["result"]

    async def tool(self, name, expected_error=None, **args):
        result = await self.call("tools/call", name=name, arguments=args)
        if expected_error:
            assert result.get("isError") and expected_error.lower() in result["content"][0]["text"].lower(), result
        else:
            assert "isError" not in result, result
        return result.get("structuredContent", result.get("content"))

    async def viewer(self, action, **kwargs):
        return await self.tool("desktop_view", view=self.view, action=action, epoch=self.epoch, **kwargs)

    async def attach(self, view=None, **kwargs):
        return await self.tool("desktop_attach", view=view or self.view, visible=self.visible, epoch=self.epoch, **kwargs)

    async def frame(self, visible=True, latest=False, **kwargs):
        if visible != self.visible:
            self.visible = visible
            self.epoch += 1
        result = await self.tool("desktop_frame", view=self.view, visible=visible, epoch=self.epoch, sessionId=self.session,
                                 after=0 if latest else self.after, **kwargs)
        if "phase" in result:
            if self.session != result["sessionId"]:
                self.after = 0
                self.session = result["sessionId"]
            self.after = result.get("seq", self.after)
        return result

    async def phase(self, name):
        deadline = time.monotonic() + 38
        while time.monotonic() < deadline:
            result = await self.frame()
            if result["phase"] == name:
                return result
            if result["phase"] == "error":
                raise AssertionError(result)
            await asyncio.sleep(0.15)
        raise AssertionError(f"Timed out waiting for {name}: {result}")

    async def host_input(self, events, **kwargs):
        return await self.tool("desktop_ui_input", view=self.view, epoch=self.epoch, sessionId=self.session, events=events, **kwargs)

    async def action(self, action, **kwargs):
        return await self.tool("desktop_input", sessionId=self.session, action=action, **kwargs)

    async def scope(self):
        # Match only a direct child of this MCP process before fault injection.
        children = Path(f"/proc/{self.proc.pid}/task/{self.proc.pid}/children")
        for _ in range(40):
            units = set()
            for pid in children.read_text().split():
                try:
                    cgroups = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                except FileNotFoundError:
                    continue
                for cgroup in cgroups:
                    unit = cgroup.rsplit("/", 1)[-1]
                    if SCOPE.fullmatch(unit):
                        units.add(unit)
            if units:
                unit, = units
                return unit
            await asyncio.sleep(0.1)
        raise AssertionError("Could not identify the scope started by this test's MCP process.")


async def scope_names():
    proc = await asyncio.create_subprocess_exec(
        SYSTEMCTL, "--user", "list-units", "--no-legend", "--plain", "--no-pager",
        "codex-gnome-shell-*.scope", stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    assert proc.returncode == 0
    return {line.split()[0] for line in stdout.decode().splitlines()
            if line.startswith("codex-gnome-shell-")}


async def check_clean(*owned_scopes):
    for _ in range(40):
        remaining = set(owned_scopes) & await scope_names()
        if not remaining:
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"Disposable systemd scopes remain: {remaining}")


def scope_stats(scope):
    stdout = subprocess.check_output(
        [SYSTEMCTL, "--user", "show", scope, "--property=ControlGroup,TasksCurrent,MemoryCurrent,"
         "MemoryPeak,MemoryMax,TasksMax,CPUUsageNSec,CPUQuotaPerSecUSec"], text=True,
    )
    return dict(line.split("=", 1) for line in stdout.splitlines())


def find_process(scope, name):
    path = Path("/sys/fs/cgroup" + scope_stats(scope)["ControlGroup"]) / "cgroup.procs"
    candidates = []
    for pid in path.read_text().split():
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except FileNotFoundError:
            continue
        if comm == name:
            candidates.append(int(pid))
    assert len(candidates) == 1, (scope, name, candidates)
    return candidates[0]


def fixture(data_home, work):
    valid = data_home / "gnome-shell-tab/extensions/audit-fixture@local"
    shutil.copytree(ROOT / "tests/fixture", valid)
    marker = work / "only-on-host"
    marker.write_text("disposable fixture")
    (valid / "host-marker.txt").write_text(str(marker))
    invalid = valid.parent / "invalid@local"
    invalid.mkdir()
    (invalid / "metadata.json").write_text("{bad}")


async def read_card(client, path):
    from PIL import Image
    image = await client.frame(latest=True)
    path.write_bytes(base64.b64decode(image["frame"], validate=True))
    with Image.open(path) as full:
        assert full.size == (1024, 640), full.size
        full.crop((165, 140, 857, 395)).save(path.with_suffix(".png"))
        # Shell can route typing into Activities search when the overview owns focus.
        full.crop((362, 49, 640, 78)).resize((1112, 116)).save(path.with_name(path.stem + "-search.png"))
    text = []
    for source, mode in ((path.with_suffix(".png"), "6"), (path.with_name(path.stem + "-search.png"), "7")):
        text.append(subprocess.check_output(
            [TESSERACT, str(source), "stdout", "--psm", mode], stderr=subprocess.DEVNULL, text=True,
        ))
    return re.sub(r"\s+", " ", " ".join(text)).upper().strip()


async def until_card(client, needles, path):
    for _ in range(24):
        text = await read_card(client, path)
        if all(part in text for part in needles):
            return text
        await asyncio.sleep(0.16)
    raise AssertionError(f"Expected {needles!r} on screen. OCR read {text!r}.")


async def negative(client):
    for line, code in ((b"{\n", -32700), (b"null\n", -32600), (b'{"jsonrpc":"2.0","id":true,"method":"ping"}\n', -32600),
                       (b'{"jsonrpc":"2.0","method":NaN}\n', -32700), (b"x" * 66000 + b"\n", -32700)):
        client.proc.stdin.write(line)
        response = await client.read()
        assert response["error"]["code"] == code, response
    assert await client.call("ping") == {}
    await client.call("tools/call", name="desktop_view", arguments=1, expected_error=-32602)
    await client.call("tools/call", name="unknown", expected_error=-32602)
    await client.call("tools/call", name="desktop_screenshot", expected_error=-32000,
                      _meta={"codex/connectionLease": {"id": "synthetic-lease"}})
    await client.call("resources/read", uri="ui://gnome-shell/absent", expected_error=-32002)
    tools = {t["name"]: t for t in client.tool_schema}
    opener = tools["open_gnome_shell"]["_meta"]
    assert opener["openai/ui"]["entrypoints"] == [{"type": "thread"}]
    assert tools["desktop_view"]["_meta"]["ui"]["visibility"] == ["app"]
    assert tools["desktop_screenshot"]["_meta"]["ui"]["visibility"] == ["model"]
    result = await client.call("resources/read", uri=opener["ui"]["resourceUri"])
    assert "__GNOME_VERSION__" not in result["contents"][0]["text"] and "Allow Codex" in result["contents"][0]["text"]


async def run_qa(work, artifacts):
    data_home = work / "data"
    fixture(data_home, work)
    saved = artifacts / "native-fixture.jpg"
    async with Client(data_home) as client:
        await negative(client)
        print("PASS malformed protocol, oversized messages, declared tool visibility and thread resource", flush=True)
        await client.tool("desktop_screenshot", expected_error="Allow Codex")
        await client.viewer("retry", expected_error="disconnected")
        first_start = await client.attach()
        assert first_start["phase"] == "starting"
        assert (await client.attach())["sessionId"] == first_start["sessionId"]
        scope = await client.scope()
        assert await client.call("ping") == {}
        await client.viewer("close")
        await client.frame(expected_error="disconnected")
        await check_clean(scope)
        print("PASS visible-tab startup, idempotent attach and close during startup", flush=True)

        start = time.monotonic()
        await client.attach()
        running = await client.phase("running")
        assert any("invalid@local" in warning for warning in running["warnings"]), running
        await until_card(client, ["ISOLATION VERIFIED", "CLICK ZERO", "SCROLL ZERO"], saved)
        print(f"PASS real GNOME start in {time.monotonic()-start:.2f}s, extension loading and isolation witness", flush=True)
        scope = await client.scope()
        assert scope_stats(scope)["TasksMax"] == "384"
        await client.host_input([
            {"type":"move","x":305,"y":224},
            {"type":"button","button":0,"pressed":True},
            {"type":"button","button":0,"pressed":False},
            {"type":"key","key":"m","pressed":True},
            {"type":"key","key":"m","pressed":False},
        ])
        await client.tool("desktop_screenshot", expected_error="Allow Codex")
        await client.viewer("share")
        # The screenshot ID is bound to this session. Invalid events never reach the worker.
        shot = await client.call("tools/call", name="desktop_screenshot", arguments={})
        assert shot["structuredContent"]["sessionId"] == client.session and shot["content"][0]["type"] == "image"
        await client.action({"type":"move","x":1024,"y":0}, expected_error="Invalid x")
        await client.action({"type":"press_keys","keys":["Bad_Keysym_246"]}, expected_error="GNOME rejected")
        await client.action({"type":"type_text","text":"test Ω"}, expected_error="US keyboard layout")
        await client.action({"type":"type_text","text":"AUDIT 42!"})
        await until_card(client, ["ISOLATION VERIFIED", "MAUDIT 42"], saved)
        await client.action({"type":"click","x":420,"y":280,"button":0})
        await client.action({"type":"scroll","x":424,"y":303,"axis":0,"steps":1})
        await until_card(client, ["CLICK ONE", "SCROLL ONE"], saved)
        await client.host_input([{"type":"key","key":"Shift_L","pressed":True}])
        await client.action({"type":"move","x":550,"y":309}, expected_error="held user input")
        await client.host_input([{"type":"release"}])
        await client.action({"type":"move","x":550,"y":309})
        await client.action({"type":"drag","x":450,"y":330,"to_x":708,"to_y":350})
        await client.frame(visible=False)
        await client.tool("desktop_screenshot", expected_error="Allow Codex")
        await client.host_input([{"type": "move", "x": 8, "y": 8}], expected_error="Show")
        await client.frame()
        await client.tool("desktop_screenshot", expected_error="Allow Codex")
        await client.viewer("share")
        await client.viewer("unshare")
        await client.tool("desktop_screenshot", expected_error="Allow Codex")
        print("PASS on-screen keyboard text, click, wheel, drag route, hold precedence, hide and revocation", flush=True)

        await client.viewer("share")
        previous_id = client.session
        attached = await client.attach()
        assert attached["sessionId"] == previous_id and attached["phase"] == "running" and not attached["shared"]
        await client.action({"type":"move","x":45,"y":60}, expected_error="Allow Codex")
        await client.frame()
        await client.viewer("share")
        await asyncio.sleep(0.4)
        await client.frame(latest=True)
        no_change = 0
        for _ in range(12):
            frame = await client.frame()
            no_change += "frame" not in frame
            await asyncio.sleep(0.1)
        assert no_change >= 6, no_change
        stats = scope_stats(scope)
        print(f"PASS reconnect without auto-share, {no_change}/12 duplicate frames withheld. "
              f"Scope peak {int(stats['MemoryPeak'])//(1024**2)} MiB and {stats['TasksCurrent']} tasks.", flush=True)

        os.kill(find_process(scope, "gnome-shell"), signal.SIGKILL)
        failure = await client.phase("error")
        assert "GNOME" in failure["problem"] or "isolated" in failure["problem"], failure
        await check_clean(scope)
        await client.viewer("retry")
        await client.phase("running")
        assert previous_id != client.session
        await client.viewer("share")
        await client.tool("desktop_input", sessionId=previous_id, action={"type":"move","x":45,"y":60},
                          expected_error="session changed")
        print("PASS compositor crash cleanup, fresh restart and stale-session action rejection", flush=True)

        scope = await client.scope()
        os.kill(find_process(scope, "python3"), signal.SIGSTOP)
        started = time.monotonic()
        failure = await client.phase("error")
        assert "stopped responding" in failure["problem"], failure
        await check_clean(scope)
        print(f"PASS frozen worker reclaimed in {time.monotonic()-started:.1f}s", flush=True)
        await client.viewer("retry")
        await client.phase("running")
        scope = await client.scope()
        # With a frozen worker, EOF alone cannot perform cleanup.
        os.kill(find_process(scope, "python3"), signal.SIGSTOP)
        client.proc.kill()
        await client.proc.wait()
        try:
            await check_clean(scope)
        except AssertionError:
            subprocess.run([SYSTEMCTL, "--user", "kill", "--kill-whom=all", "--signal=KILL", scope],
                           timeout=6, check=False, capture_output=True)
            raise
        print("PASS abrupt MCP death reclaims a frozen worker and its entire desktop scope", flush=True)

    # An independent chat starts fresh after process death.
    async with Client(data_home) as second:
        assert (await second.attach())["phase"] == "starting"
        await second.phase("running")
        scope = await second.scope()
        await second.viewer("close")
        await second.frame(expected_error="disconnected")
    await check_clean(scope)
    print("PASS independent fresh connection and orderly shutdown", flush=True)


if __name__ == "__main__":
    if TESSERACT is None:
        raise SystemExit("Install tesseract for pixel-verified integration tests.")
    output = ROOT / "evidence"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gnome-tab-qa-") as work:
        asyncio.run(run_qa(Path(work), output))
