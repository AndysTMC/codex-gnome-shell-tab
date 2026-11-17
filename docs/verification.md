# Verification

GNOME Shell for Codex 0.3.0 was verified on 2026-11-17 on x86-64 Ubuntu 26.04.1 LTS with GNOME Shell 50.1. The local Codex desktop build was 26.1111.21308, with bundled Codex CLI 0.169.0-alpha.11.1. The tests use temporary GNOME homes. The Codex test creates a disposable configuration.

## Run the checks

Run the quick tests from the repository root with Python 3.11 or newer.

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

The native integration suite starts real GNOME guest processes on the Linux host. It needs the runtime requirements, Pillow for system Python, and Tesseract with English language data to verify visible behavior in captured frames. On Ubuntu, the extra test packages are `python3-pil`, `tesseract-ocr`, and `tesseract-ocr-eng`.

```sh
/usr/bin/python3 -B -u tests/gnome_integration.py
```

The Codex suite verifies install, discovery, routing, and removal in a temporary profile. Use the binary bundled with your desktop build when checking desktop compatibility. Add `--live` to exercise two real guests and their shared controls through the app server. The live mode requires the same Linux and GNOME runtime as the plugin.

```sh
python3 -B -u tests/codex_app_server.py --codex /path/to/codex
python3 -B -u tests/codex_app_server.py --codex /path/to/codex --live
```

Run the native suites one at a time so startup and cleanup timings are comparable. Native diagnostic images are written to the ignored `evidence/` directory.

## Checks and results for 0.3.0

| Check | Verified result |
| --- | --- |
| Session and input tests | Fourteen tests passed, including timed-out views, cold starts, hidden-tab heartbeats, stale visibility and session IDs, revocation during requests, and cleanup. |
| Real GNOME guest and OCR | A fixture extension verified isolation and showed visible changes from keyboard, mouse, scroll, and drag actions. Forced compositor exit, a frozen worker, and abrupt MCP exit were followed by cleanup. |
| Browser-host test | A test host with sandboxed iframes verified automatic startup, the near-black guest background, mouse and keyboard input, two independent tabs, switching, sharing, and reopening. An active iframe removed without a teardown message was cleaned up in about 25 seconds. |
| Bundled Codex app server | Loaded the installed plugin and declared UI resource. Separate chats rejected one another's view tokens. Two guests in the same chat stayed separate, and the screenshot tool selected the shared one. Closing one left the other available. |
| Actual Codex desktop | The installed launcher opened two separate desktops automatically from New tab in the same local chat. |
| Package lifecycle | The local CLI upgraded 0.2.0 to 0.3.0 in an isolated profile and preserved unrelated settings. Installation and removal worked in a disposable profile. The local 0.3.0 install was checked against the release contents. |

The browser test used an MCP Apps test host. Use the desktop checklist below to check renderer behavior on your Codex build.

## Desktop smoke test

1. Install the checkout and open a fresh local chat. Select **GNOME Shell** in **New tab** and wait for the automatically started desktop.
2. Open a second GNOME Shell tab. Change something visible in the first desktop, then switch between the tabs and confirm each keeps its own state.
3. In the visible tab, click **Allow Codex**. Ask Codex to describe its screen and perform a harmless mouse or keyboard action. Switch away and check that a subsequent screenshot request asks for a new grant.
4. Close one tab and confirm the other stays open. Close the last tab and verify the guest scope disappears. Open a new tab to confirm that it gets a fresh desktop.
5. Remove the plugin and verify that the launcher disappears in a fresh chat.

Compatibility evidence is limited to the Linux, GNOME, and Codex versions listed above. Other distributions, later GNOME major versions, keyboard layouts beyond US, and individual extensions need their own checks.
