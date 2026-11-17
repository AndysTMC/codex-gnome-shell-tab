# Security

The plugin is intended for disposable local desktops and trusted GNOME Shell extensions. It relies on the host's Linux kernel, Bubblewrap, systemd, and Codex app. The guest shares the host kernel. Use the project's isolation model when deciding which code and data to open in a guest.

## Guest boundaries

- Every tab gets a fresh home and its own process, user, mount, network, and IPC namespaces. The plugin starts private D-Bus, Wayland, and PipeWire services inside that guest. Guest data is discarded when the scope terminates.
- The guest has access to read-only system files under `/usr`, with `/usr/local` masked, and selected host font and loader configuration files. The optional extension directory is also mounted read-only. Installed system applications can run in the guest when their requirements are available.
- The primary home directory, host clipboard, host display and D-Bus sockets, GPU, and audio devices are outside the guest's mounts. The guest has no host network access. The production plugin communicates over stdio without opening an HTTP listener.
- The launcher requires its Bubblewrap and systemd protections before it starts a desktop. Each user scope has a 1536 MiB memory limit, a 384-task limit, and a CPU quota equivalent to two cores. These limits apply per guest. The extension store is shared read-only across guests and is best kept exclusively for trusted extensions and their assets.

## Sharing and retention

Codex gets access to screenshots and input only after you click **Allow Codex** in a visible GNOME Shell tab. The tab uses a trusted click and the server checks the active grant, desktop ID, and visibility state. A hidden or disconnected tab releases held input and loses the grant. Granting access in a second tab ends the first grant in the same chat.

Revoking a grant stops subsequent reads and input. An input event already handed to Mutter can still complete. Screenshots already returned to Codex may remain in the conversation. Treat visible content, including text rendered by extensions, as guest data. The plugin limits input to supported pointer and keyboard events in the guest.

The Codex host and local user are trusted. They control the MCP connection and install the launcher. A separate virtual machine is appropriate for hostile workloads that require stronger isolation.

## Report a vulnerability

Use **Security → Report a vulnerability** on the GitHub repository when private reporting is available. Otherwise, contact the repository owner privately. Include the plugin version, GNOME and Codex versions, the affected boundary, and a minimal description using disposable data. Share sensitive logs and screenshots through the private channel.
