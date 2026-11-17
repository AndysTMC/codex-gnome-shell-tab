# Troubleshooting

## GNOME Shell is missing from New tab

Register and install the plugin from a local checkout. Open a fresh local chat and look under **New tab → More tools → Plugins and MCPs**. The exact menu grouping depends on the Codex build. Confirm the marketplace and plugin identity using the [install instructions](../README.md#install).

## Codex shows “Couldn't display GNOME Shell”

A chat that was open before installation can still use its earlier MCP configuration. Reopen the plugin in a fresh local chat first. If the plugin works there, close any running GNOME tabs before restarting Codex to refresh older chats. If a fresh chat fails too, check the Codex build and installation receipt.

## The tab shows “Desktop unavailable”

Use the message under that heading to start diagnosis. Verify that Codex is running as your desktop user and can reach an active systemd user manager. Check the GNOME major version and whether Bubblewrap can create user and network namespaces.

```sh
gnome-shell --version
systemctl --user list-units --type=scope
/usr/bin/bwrap --unshare-user --unshare-net --ro-bind / / /usr/bin/true
```

If the namespace check fails, review your distribution's Bubblewrap package and user namespace policy. Keep the launcher protections in place. For a GStreamer or PipeWire error, check the runtime packages in the [requirements](../README.md#requirements). The MCP server logs worker startup errors to stderr, which your Codex build may collect in its logs.

**Try again** starts a fresh desktop after a worker failure. Temporary files from the failed guest are discarded during cleanup.

## GNOME shows “Screen Lock disabled”

The headless guest runs without GNOME Display Manager. GNOME can show this notice on startup. Close the tab when you want to end the session and discard its temporary data.

## Codex cannot see or control a desktop

Make the intended GNOME Shell tab visible and click **Allow Codex**. Changing tabs revokes the current grant. If a tool reports that the desktop session changed, request a new screenshot before sending input. While you hold a mouse button or key in the guest, complete that interaction before Codex tries to act.

## Keyboard input goes to Codex or a shortcut does not work

Click inside the live desktop. The guest starts with a US keyboard layout. Some shortcuts are reserved by the host, and IME input or characters outside the guest layout may be unavailable. Use the guest's on-screen menus when a shortcut is intercepted. Click outside the desktop or press **Ctrl+Alt+Shift+Esc** to return keyboard focus to the tab controls.

## A closed desktop still appears in the process list

The close notification triggers immediate shutdown. If Codex removes the view first, its worker expires after the last heartbeat. This takes about 25 seconds for an active tab or up to 2½ minutes for a background tab.

```sh
systemctl --user list-units 'codex-gnome-shell-*.scope'
```

If a scope remains beyond that interval with the plugin closed, capture the error and the running versions for an issue. Leave any unrelated user scopes alone.

## An extension is missing or reports an error

Make sure the extension supports GNOME Shell 50, its directory name matches its metadata UUID, and it includes its required assets and any compiled schemas. Open a new tab after changing the extension store. Network services, hardware, and the primary GNOME session's display and D-Bus services are outside the guest. Check the extension's requirements against the [guest boundaries](../SECURITY.md#guest-boundaries).
