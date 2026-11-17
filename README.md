# GNOME Shell for Codex

Open a separate GNOME desktop inside a local Codex chat on Linux. Choose **GNOME Shell** from **New tab** and the desktop starts automatically. Use it with your mouse and keyboard, or choose **Allow Codex** to share the visible desktop.

## Requirements

- A Linux build of Codex desktop with local chats, MCP Apps, and support for local plugin tabs. The CLI handles installation. The interactive desktop opens in the Codex app.
- GNOME Shell 50, a running systemd user manager, and Bubblewrap with unprivileged user namespaces. The plugin uses private Mutter interfaces tied to GNOME Shell 50.
- System Python 3.11 or newer at `/usr/bin/python3`, PyGObject, PipeWire, WirePlumber, D-Bus, GStreamer, and libxkbcommon.

On Ubuntu with GNOME Shell 50 available, use APT to install any missing runtime packages.

```sh
sudo apt install bubblewrap dbus gnome-shell libxkbcommon0 pipewire +  python3 python3-gi wireplumber gir1.2-gstreamer-1.0 +  gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

The release was tested on x86-64 Ubuntu 26.04.1 LTS with GNOME Shell 50.1 and Codex desktop build 26.1111.21308. See the [test record](docs/verification.md) and [troubleshooting guide](docs/troubleshooting.md).

## Install

Clone or extract this repository to a directory you can keep. Run these commands from that directory.

```sh
codex plugin marketplace add "$PWD"
codex plugin add gnome-shell@local-gnome-shell
```

You can also install **GNOME Shell** from the **Local GNOME Shell** marketplace in the Codex Plugins Directory after registering it. Open a fresh local chat and choose **New tab → More tools → Plugins and MCPs → GNOME Shell**. Some builds show **Plugins and MCPs** directly on the New tab page.

## Use the desktop

1. Select **GNOME Shell** in **New tab**. A short loading screen gives way to the desktop with a plain near-black background.
2. Click inside the desktop to send it keyboard input. Press **Ctrl+Alt+Shift+Esc** to return focus to the tab controls.
3. Use **Allow Codex** to let Codex see the screen and send input. Use **Revoke Codex access** or switch away to stop sharing. Screenshots returned to Codex may appear in the chat.
4. Open GNOME Shell again from **New tab** to get a separate desktop. A chat can have three desktops open and share one with Codex at a time. Switch back to resume a running desktop.
5. Close a GNOME Shell tab to shut down its desktop and discard its temporary files. If the host drops the tab without a close notification, cleanup takes about 25 seconds for an active tab or up to 2½ minutes for a background tab.

The guest is offline and has a temporary home, a private Wayland display, and private D-Bus and PipeWire services. The guest keyboard starts in the US layout. The [security model](SECURITY.md) describes which files and services the guest can reach.

## Use a GNOME Shell extension

Place an unpacked GNOME Shell 50 extension in `~/.local/share/gnome-shell-tab/extensions/<extension-uuid>/`, or under the corresponding `gnome-shell-tab/extensions/` directory in your absolute `XDG_DATA_HOME` if Codex inherits it.

The directory name must match the `uuid` in `metadata.json`. Include any assets and compiled schemas the extension normally uses. Open a new GNOME Shell tab to load the extension. Keep only files you intend to expose to the guest in the extension directory. Extensions that need a network or the primary desktop's services may have limited functionality.

## Update or remove

Close any GNOME Shell tabs before updating. Update the existing checkout, run the install command again, and open GNOME Shell in a fresh local chat.

```sh
git pull --ff-only
codex plugin add gnome-shell@local-gnome-shell
```

To remove the plugin, close its tabs and run the first command below, or uninstall from the Codex Plugins Directory. The second command also removes the marketplace registration. The optional extension directory is yours to manage.

```sh
codex plugin remove gnome-shell@local-gnome-shell
codex plugin marketplace remove local-gnome-shell
```

## Project documentation

- Follow the tab, tools, screen capture, and input through the code. [Architecture](docs/architecture.md)
- Diagnose startup, input, sharing, and shutdown problems. [Troubleshooting](docs/troubleshooting.md)
- Reproduce the checks and see what was verified on the reference machine. [Verification](docs/verification.md)
- Develop, test, and package a release. [Contributing](CONTRIBUTING.md)
- Understand the guest boundary and report a vulnerability. [Security](SECURITY.md)
- Review release changes. [Changelog](CHANGELOG.md)

## License

Licensed under the [MIT License](LICENSE).
