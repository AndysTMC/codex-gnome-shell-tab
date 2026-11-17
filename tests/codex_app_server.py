"""Exercise real Codex plugin catalog and thread routing in an isolated CLI profile."""

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "gnome-shell@local-gnome-shell"
VERSION = json.loads((ROOT / "gnome-shell/plugin.json").read_text())["version"]


def cli(codex, env, *args):
    proc = subprocess.run([codex, "plugin", *args, "--disable", "remote_plugin", "--json"],
                          env=env, timeout=40, text=True, capture_output=True)
    assert proc.returncode == 0, (args, proc.returncode, proc.stderr[-3200:], proc.stdout[-1800:])
    return json.loads(proc.stdout)


class AppServer:
    def __init__(self, codex, env, workdir):
        self.codex = codex
        self.env = env
        self.workdir = workdir
        self.proc = None
        self.id = 0

    async def __aenter__(self):
        self.proc = await asyncio.create_subprocess_exec(
            self.codex, "app-server", "--stdio", "--enable", "enable_mcp_apps", "--disable", "remote_plugin",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            env=self.env, limit=2 * 1024**2,
        )
        try:
            await self.request("initialize", clientInfo={"name": "gnome-shell-qa", "version": VERSION},
                               capabilities={"experimentalApi": True})
            self.proc.stdin.write(b'{"method":"initialized"}\n')
        except BaseException:
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *_):
        self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), 10)
        except TimeoutError:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def request(self, method, **params):
        self.id += 1
        identifier = self.id
        self.proc.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}).encode() + b"\n")
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), 45)
            assert line, f"App-server exited while waiting for {method}"
            reply = json.loads(line)
            if reply.get("id") == identifier and "method" not in reply:
                assert "error" not in reply, (method, reply)
                return reply["result"]

    async def thread(self):
        result = await self.request("thread/start", cwd=str(self.workdir), ephemeral=True)
        return result["thread"]["id"]

    async def inventory(self, thread):
        result = await self.request("mcpServerStatus/list", threadId=thread, detail="full")
        matches = [server for server in result["data"] if "gnome-shell" in server["name"]]
        assert len(matches) == 1, (result["data"], matches)
        return matches[0]

    async def tool(self, thread, server, tool, **args):
        return await self.request("mcpServer/tool/call", threadId=thread, server=server,
                                  tool=tool, arguments=args)


async def exercise(codex, env, workdir, *, live):
    async with AppServer(codex, env, workdir) as app:
        listing = await app.request("plugin/installed")
        assert PLUGIN in json.dumps(listing), listing
        thread = await app.thread()
        inv = await app.inventory(thread)
        assert inv["serverInfo"]["version"] == VERSION, inv["serverInfo"]
        if "serverCapabilities" in inv:
            assert "io.modelcontextprotocol/ui" in inv["serverCapabilities"]["extensions"]
        tools = inv["tools"]
        opener = tools["open_gnome_shell"]
        uri = opener["_meta"]["ui"]["resourceUri"]
        assert opener["_meta"]["ui"]["visibility"] == ["app"]
        assert opener["_meta"]["openai/ui"]["entrypoints"] == [{"type": "thread"}], opener
        result = await app.request("mcpServer/resource/read", threadId=thread, server=inv["name"], uri=uri)
        assert "Allow Codex" in json.dumps(result) and "text/html;profile=mcp-app" in json.dumps(result), result.keys()
        print("PASS actual Codex app-server loaded installed stdio MCP, thread metadata, and UI resource", flush=True)

        async def tool(name, **args):
            return await app.tool(thread, inv["name"], name, **args)

        unavailable = await tool("desktop_screenshot")
        assert unavailable.get("isError"), unavailable
        view = uuid.uuid4().hex
        attached = await tool("desktop_attach", view=view, visible=False, epoch=0)
        assert attached["structuredContent"]["phase"] == "stopped", attached
        other_view = uuid.uuid4().hex
        assert not (await tool("desktop_attach", view=other_view, visible=False, epoch=0)).get("isError")
        other_thread = await app.thread()
        other_inv = await app.inventory(other_thread)
        assert other_inv["name"] == inv["name"]
        assert (await app.tool(other_thread, inv["name"], "desktop_frame", view=view, visible=False, epoch=0,
                               sessionId=None, after=0))["isError"]
        side = await app.tool(other_thread, inv["name"], "desktop_attach", view=other_view, visible=False, epoch=0)
        assert side["structuredContent"]["phase"] == "stopped"
        print("PASS Codex tool calls preserve the owning thread across calls for multiple tabs and isolate a second thread", flush=True)
        if not live:
            await tool("desktop_view", view=view, action="close", epoch=0)
            print("Guest startup is available with --live.", flush=True)
            return
        started = await tool("desktop_attach", view=view, visible=True, epoch=1)
        session_id = started["structuredContent"]["sessionId"]
        frames = {}
        for _ in range(150):
            sample = await tool("desktop_frame", view=view, visible=True, epoch=1, sessionId=session_id, after=0)
            frames = sample["structuredContent"]
            if frames["phase"] == "running":
                break
            assert frames["phase"] == "starting", frames
            await asyncio.sleep(0.2)
        assert frames["phase"] == "running" and len(frames["frame"]) > 1000, frames["phase"]
        await tool("desktop_view", view=view, action="share", epoch=1)
        screenshot = await tool("desktop_screenshot")
        assert screenshot["structuredContent"]["sessionId"] == session_id
        assert screenshot["content"][0]["type"] == "image"
        action = await tool("desktop_input", sessionId=session_id,
                            action={"type":"press_keys","keys":["Super_L"]})
        assert not action.get("isError"), action
        second = await tool("desktop_attach", view=other_view, visible=True, epoch=1)
        second_id = second["structuredContent"]["sessionId"]
        assert second_id != session_id
        for _ in range(150):
            await tool("desktop_frame", view=view, visible=False, epoch=2, sessionId=session_id, after=0)
            sample = await tool("desktop_frame", view=other_view, visible=True, epoch=1, sessionId=second_id, after=0)
            state = sample["structuredContent"]
            if state["phase"] == "running":
                break
            assert state["phase"] == "starting", state
            await asyncio.sleep(0.2)
        assert state["phase"] == "running" and len(state["frame"]) > 1000
        assert (await tool("desktop_screenshot"))["isError"]
        await tool("desktop_view", view=other_view, action="share", epoch=1)
        assert (await tool("desktop_screenshot"))["structuredContent"]["sessionId"] == second_id
        assert (await tool("desktop_input", sessionId=session_id,
                           action={"type":"press_keys", "keys":["Escape"]}))["isError"]
        # A second chat must have a separate MCP connection and no share grant.
        other = await app.thread()
        other_inv = await app.inventory(other)
        assert other_inv["name"] == inv["name"]
        other_shot = await app.tool(other, inv["name"], "desktop_screenshot")
        assert other_shot.get("isError"), other_shot
        another_view = await app.tool(other, inv["name"], "desktop_attach", view=uuid.uuid4().hex, visible=False, epoch=0)
        assert another_view["structuredContent"]["phase"] == "stopped"
        assert not (await tool("desktop_view", view=other_view, action="close", epoch=1)).get("isError")
        existing = await tool("desktop_frame", view=view, visible=True, epoch=3, sessionId=session_id, after=0)
        assert existing["structuredContent"]["sessionId"] == session_id
        assert not existing["structuredContent"]["shared"]
        assert not (await tool("desktop_view", view=view, action="close", epoch=3)).get("isError")
        print("PASS actual Codex app-server started two isolated desktops in one thread, "
              "preserved the background desktop, routed screenshots to the allowed tab and closed both", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Path to the Codex binary")
    parser.add_argument("--live", action="store_true", help="Start real GNOME guests")
    args = parser.parse_args()
    codex = shutil.which(args.codex)
    if codex is None:
        parser.error("Codex executable not found")

    with tempfile.TemporaryDirectory(prefix="gnome-tab-codex-test-") as scratch:
        profile = Path(scratch).resolve()
        workdir = profile / "workspace"
        workdir.mkdir()
        config = profile / "config.toml"
        env = {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "LC_ALL") if key in os.environ}
        env.update(CODEX_HOME=str(profile), XDG_DATA_HOME=str(profile / "data"),
                   XDG_CONFIG_HOME=str(profile / "config"), XDG_CACHE_HOME=str(profile / "cache"))
        config.write_text('[analytics]\nenabled = false\n'
                          f'[projects.{json.dumps(str(workdir), ensure_ascii=False)}]\ntrust_level = "trusted"\n')
        before = tomllib.loads(config.read_text())

        cli(codex, env, "marketplace", "add", str(ROOT))
        receipt = cli(codex, env, "add", PLUGIN)
        cache = Path(receipt["installedPath"]).resolve()
        assert cache.is_relative_to(profile), cache
        assert (cache / "mcp.json").is_file() and os.access(cache / "launch-gnome-shell", os.X_OK)
        assert receipt["pluginId"] == PLUGIN and receipt["version"] == VERSION, receipt
        print("PASS CLI installed the expected version and preserved launcher permissions", flush=True)
        try:
            asyncio.run(exercise(codex, env, workdir, live=args.live))
        finally:
            cli(codex, env, "remove", PLUGIN)
            cli(codex, env, "marketplace", "remove", "local-gnome-shell")
        after = tomllib.loads(config.read_text())
        assert all(after.get(key) == value for key, value in before.items()), after.keys()
        assert PLUGIN not in after.get("plugins", {}) and not cache.exists()
        print("PASS installation and removal preserved unrelated settings in the disposable profile", flush=True)


if __name__ == "__main__":
    main()
