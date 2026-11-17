# Changelog

## 0.3.0 · 2026-11-17

- Start GNOME Shell automatically when its tab becomes visible. Show a loading indicator during startup and a retry action on failure.
- Give each tab its own desktop. Keep background desktops available and clean up workers when tabs close or disconnect.
- Fit the desktop to the panel and use a plain near-black guest background.
- Route Codex screenshots and input to the explicitly shared desktop. Revoke access when the tab is hidden or replaced.

## 0.2.0 · 2026-11-17

- Package the local Codex plugin with a GNOME Shell entrypoint in New tab.
- Run an isolated headless GNOME session with screen capture, local mouse and keyboard input, and opt-in sharing with Codex.
- Add support for unpacked GNOME Shell 50 extensions in a dedicated local directory.
