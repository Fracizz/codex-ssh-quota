# Codex SSH Quota

[简体中文](README.zh-CN.md) · [MIT](LICENSE)

View recorded Codex quota from an SSH environment inside Codex Desktop. Reuse an existing SSH connection, read the latest quota snapshot from session files, and keep a ten-minute cache with manual refresh and offline fallback.

The panel shows the source host, recorded remaining percentage, capture time, and reset time. These are historical snapshots. Account switches and reset windows can make an old snapshot stale.

## Install on the right execution host

Requires Python 3.10+, a Codex CLI with plugin support, and a desktop client supporting MCP Apps / OpenAI MCP Extensions. The installer can discover the bundled Windows CLI. SSH mode also needs OpenSSH Client, a trusted host key, non-interactive authentication, and Python 3 on the remote host.

### Query a remote host from a desktop-local chat

```powershell
git clone https://github.com/Fracizz/codex-ssh-quota.git
cd codex-ssh-quota
python install.py --ssh-host my-codex-server
```

Use an existing SSH alias from your SSH configuration. The installer stages a private `.runtime` copy with your Python launch path and SSH settings, then registers and installs it using the Codex plugin CLI. Personal settings stay out of tracked source files. Keep the checkout for updates.

### Use the plugin in a chat executing on an SSH host

**Install on the SSH execution host too.** A Windows-only installation does not automatically expose its tools to a remotely executing chat.

Run in the SSH terminal:

```bash
git clone https://github.com/Fracizz/codex-ssh-quota.git
cd codex-ssh-quota
python3 install.py --local
```

`--local` reads snapshots directly on the current execution host. You can install on both the desktop and the SSH host for their respective chats.

Open a new chat on the configured host and select **Codex SSH 额度**. Restart the desktop client after current work finishes if it retains old plugin metadata. Hosts with text-only tool support can use the structured quota response; the panel requires the UI extensions.

## Windows main-window quota bar

To display the quota directly over the Codex title area:

```powershell
python install.py --ssh-host my-codex-server --hud
```

This starts a separate Tk/Win32 companion window anchored to the foreground Codex/ChatGPT desktop window. It follows window movement and DPI changes, and hides when another app takes focus or the client is minimized. It shows the configured SSH source's snapshot; it does not automatically switch SSH sources when you change chats.

Left-click opens details. Right-click offers refresh and close. The bar reuses the ten-minute cache and starts no model turns. It does not modify or inject code into the desktop client.

The companion stays running for the current Windows login session. After restarting Windows, start it again with:

```powershell
pythonw .runtime/plugin/hud.py
# Stop the companion:
python .runtime/plugin/hud.py --stop
```

Tkinter is required only for the Windows companion and is included in the standard python.org Windows installer. The MCP panel and SSH-host mode do not need Tkinter.

## Check, update, and remove

```powershell
python .runtime/plugin/server.py --probe
git pull --ff-only
python install.py --ssh-host my-codex-server
codex plugin remove codex-ssh-quota@codex-ssh-quota
codex plugin marketplace remove codex-ssh-quota
```

Repeat `--local` when updating the SSH-host installation. You can also copy `settings.example.json` to ignored `settings.json`. `CODEX_QUOTA_SSH_HOST` and `CODEX_QUOTA_MODE` override configured settings.

After verifying this version, prototype users can remove `codex-quota@codex-quota-local` and its `codex-quota-local` marketplace.

## Data handling and limits

- Inspect up to 64 session files from the last 14 days, reading at most the last 512 KiB of each file. Select the latest `token_count.rate_limits` event.
- Session tails can contain conversation text; only whitelisted quota fields, host information, and capture time are returned. The collector does not read login tokens, query the live quota API, or start model turns.
- SSH uses existing configuration with `BatchMode` and strict host-key checking. It never accepts unknown host keys automatically.
- Refresh every ten minutes; manual refresh bypasses the cache. Failed reads retain the previous cached snapshot with a status message. Expired windows are labeled without assuming a new percentage.
- MCP requests are limited to 1 MiB per message. Quota responses are limited to 20 buckets / 32 KiB. Cache location: `%LOCALAPPDATA%/CodexSSHQuota` on Windows, `~/.cache/CodexSSHQuota` on Linux.

## Related projects

| Project | Focus |
| --- | --- |
| [CodexBar](https://github.com/steipete/CodexBar) | Multi-provider usage in the macOS menu bar |
| [Codex Usage](https://github.com/jayhilwig/codexusage) | Windows/macOS title-area companion HUD |
| [CodexCast](https://github.com/digitdash/codexcast) | macOS Codex plugin with quota, credits, and active-hours forecasts |
| [Usage Meter](https://github.com/zhaoshuoxp/agent-tools) | Sidebar indicator and detail panel for a locally signed-in account |

This project focuses on recorded quota in an existing SSH environment, keeping remote login credentials on the remote host.

## Development

```bash
python3 -m unittest -q
python3 smoke_install.py
```

Unit tests use synthetic data and start no model requests or real SSH connections. `smoke_install.py` requires Codex CLI and checks actual installation and tool discovery in a disposable `CODEX_HOME`. CI covers Windows/Linux with Python 3.10, 3.12, and 3.14. Visible panel rendering still needs acceptance on a compatible desktop client.

[Thread entrypoint specification](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#thread-entrypoint) · [Desktop plugin packaging](https://developers.openai.com/plugins/deploy/desktop-only)
