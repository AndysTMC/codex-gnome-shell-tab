# GNOME Shell for Codex

GNOME Shell opens a disposable desktop in a **local Codex chat**. You can use it from the tab and choose when Codex can see the screen and send input.

## Install and remove

The local marketplace source has a `.agents/plugins/marketplace.json` next to `gnome-shell/`. Keep that source in a permanent directory you control.

```sh
codex plugin marketplace add /absolute/path/to/gnome-shell-tab
codex plugin add gnome-shell@local-gnome-shell
```

The Codex desktop Plugins Directory also supports installing from the **Local GNOME Shell** marketplace. Use a new local chat or reload plugins if the active chat predates installation. Open **New tab → More tools → Plugins and MCPs → GNOME Shell**. Some builds display **Plugins and MCPs** directly under the New tab menu.

To uninstall, close any GNOME Shell tabs, then remove the plugin through the Plugins Directory or use these commands. The optional second command removes this plugin's marketplace registration.

```sh
codex plugin remove gnome-shell@local-gnome-shell
codex plugin marketplace remove local-gnome-shell
```

## Use the desktop

1. Select **GNOME Shell** in **New tab**. The desktop starts as soon as the tab is visible. Click the desktop to direct the keyboard there. Press **Ctrl+Alt+Shift+Esc** to return focus to the tab controls.
2. Click **Allow Codex** if you want help. Codex can request screenshots and send input while this tab is visible. Requested screenshots can be included in your chat. Use **Revoke Codex access** to end sharing. Switching away from the tab also revokes sharing.
3. Open another GNOME Shell tab to get a separate desktop. Each chat supports up to three desktops. Codex shares one desktop per chat, chosen with **Allow Codex** in that tab.
4. Switch between open tabs to return to each desktop. Close a tab to discard its desktop and data. If the host drops a tab without a close notification, cleanup takes about 25 seconds for the active tab or up to 2½ minutes for a background tab.

## Requirements and scope

- Linux with an active systemd user manager, unprivileged user namespaces and Bubblewrap. The plugin requires these protections to start and instructs you to check the dependency or MCP server log on failure.
- GNOME Shell 50 with Mutter's private RemoteDesktop and ScreenCast D-Bus interfaces. Tested on Ubuntu with GNOME Shell 50.1. A GNOME upgrade can change these interfaces.
- System Python 3.11 or newer, PyGObject and GStreamer introspection, GStreamer PipeWire, app, video conversion and JPEG elements, WirePlumber, D-Bus and libxkbcommon. On Ubuntu the relevant packages include `python3-gi`, `gir1.2-gstreamer-1.0`, `gstreamer1.0-pipewire`, `gstreamer1.0-plugins-base` and `gstreamer1.0-plugins-good`.
- A local Codex desktop build with MCP Apps, app-to-server tool calls, thread tabs, and a dedicated stateful MCP connection for the chat. The GNOME compositor and the MCP server run on the local computer.

The desktop has a temporary home, private buses and display, and no network, host clipboard, GPU or audio devices. Installed system applications and opt-in extensions are exposed via read-only paths. Everything inside the temporary home is discarded on stop. Keyboard input supports characters and named keys available in the default US layout. Changing the guest layout, using IMEs, and pressing host-reserved shortcuts can affect which keys reach the desktop. This is a Linux namespace sandbox for local development and collaboration. Install GNOME extensions you trust.

### Use an extension

Place an unpacked, GNOME-50-compatible extension in your XDG data directory under `gnome-shell-tab/extensions/<extension-uuid>/`. On a default Ubuntu install this is `~/.local/share/gnome-shell-tab/extensions/<extension-uuid>/`. The directory name must match the `uuid` in its `metadata.json`. Include the extension's usual compiled schemas and runtime assets. Start a new desktop to pick it up. Extensions that require networking, host services, native hardware or the primary desktop's settings have limited functionality here.
