# Contributing

Make changes in the repository's `gnome-shell/` directory. Codex caches installed plugin versions. Update the source and reinstall with `codex plugin add gnome-shell@local-gnome-shell` when checking changes in the app. Close existing desktop tabs before upgrading and use a fresh local chat for the verification.

## Development

- The MCP server uses the Python standard library. The desktop worker uses the system GNOME, PyGObject, PipeWire, and GStreamer stack. The tab is one HTML file served by the MCP server.
- Use the [architecture guide](docs/architecture.md) to locate the code for a change. Preserve chat isolation, explicit sharing, and the separation between foreground access and background session lifetime.
- Run the quick test suite for Python and package changes. Run the native or Codex suites for changes to worker startup, capture, input, sharing, or tool routing. Use the [desktop checklist](docs/verification.md#desktop-smoke-test) for changes visible in a tab.
- Report ordinary bugs with your plugin version, environment, and expected and observed behavior. Use the private route in [Security](SECURITY.md#report-a-vulnerability) for possible escapes, data exposure, or authorization bugs.

## Package a release

Set the same version in [`plugin.json`](gnome-shell/plugin.json) and [`.codex-plugin/plugin.json`](gnome-shell/.codex-plugin/plugin.json), and record the changes in the [changelog](CHANGELOG.md). Keep the two MCP manifests and the marketplace source consistent. The launcher must be executable in Git.

Run the verification suite and commit the release changes before building an archive. Archive the committed tree to include the local marketplace, plugin, and documentation.

```sh
git archive --format=zip --prefix=gnome-shell-tab/ -o gnome-shell-tab.zip HEAD
sha256sum gnome-shell-tab.zip
```

Extract the archive into a new directory and verify the launcher is executable there. Register the extracted directory and test it in a disposable Codex profile using the [verification guide](docs/verification.md). Use the release commit or tag when publishing an archive.
