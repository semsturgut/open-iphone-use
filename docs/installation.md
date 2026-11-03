# Installation and removal

Open iPhone Use runs on macOS. It needs Xcode 27 or later; the iPhone needs iOS 27 or later
with Developer Mode on. For Xcode 26 / iOS 26, install the
[v0.0.3 release](https://github.com/semsturgut/open-iphone-use/releases/tag/v0.0.3), which uses
Bluetooth and Safari screen sharing.

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash
open-iphone-use serve
open-iphone-use new
```

To uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --uninstall
```

## Options

The installer uses the latest published release, or `main` if none exists. `--version TAG`
selects a release tag or branch, `--dry-run` prints the plan without writing files, and
`--no-agents` skips MCP registration. `OIU_REPO` and `OIU_ARCHIVE_URL` select a repository or a
full archive URL (`file://` works for local archives).

For development, clone the repository and run `sh setup.sh` from the checkout.

The managed runtime is `~/.local/share/open-iphone-use/runtime`, the launcher is
`~/.local/bin/open-iphone-use`, and phone state stays separately in `~/.open-iphone-use`. Add
`~/.local/bin` to PATH if your shell does not already include it; no shell profile is rewritten.
Unrelated occupied CLI paths are refused.

Setup uses Python 3.11+ if present. Otherwise it installs a private CPython 3.12 with Astral's
`uv` inside the managed runtime, without changing the system Python. Dependencies, including
pymobiledevice3, come from PyPI. Run the server as your normal user, not root.

```sh
sh setup.sh --no-agents                  # runtime/CLI only
sh setup.sh --uninstall                  # keeps phone state
OIU_INSTALL_ROOT=/private/path/runtime OIU_BIN_DIR=/private/path/bin sh setup.sh
OIU_PYTHON=/path/to/python3 sh setup.sh
```

`OIU_INSTALL_ROOT` and `OIU_BIN_DIR` must be absolute. Managed directories are marked;
unmanaged directories and unrelated CLI files are preserved. Stop a running server before
upgrading.

## Agent registration and Skill

Detected clients receive only this product's stdio MCP entry:

| Client | Config | Entry |
| --- | --- | --- |
| Codex | `~/.codex/config.toml` | `mcp_servers.open-iphone-use` |
| Claude Code | `~/.claude.json` | `mcpServers.open-iphone-use` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers.open-iphone-use` |
| OpenCode | `~/.config/opencode/opencode.jsonc` (preferred when present), or `.json` | `mcp.open-iphone-use`: local command array, enabled |
| Command Code | `~/.commandcode/mcp.json` | `mcpServers.open-iphone-use`: stdio transport, enabled |

Every command names the installed venv CLI and `mcp`. Legacy `/Applications/Open iPhone Use.app/…`
entries are replaced. Unrelated settings and approval policies remain intact. OpenCode
comments, trailing commas and surrounding text are preserved. Each distinct pre-edit config
has a private backup under `~/.local/share/open-iphone-use/registrations`; syntax errors and
symlinked configs are refused. Backups may contain private settings: never publish them.

The only installed canonical skill is `~/.agents/skills/open-iphone-use/SKILL.md` (file 644,
directory 755 on POSIX). Detected Claude/Command Code clients get directory aliases at
`~/.claude/skills/open-iphone-use` and `~/.commandcode/skills/open-iphone-use`, targeting
`../../.agents/skills/open-iphone-use`. The runtime's `agent-skill.md` is a source template, not another discovered skill.
Registration replaces stale product copies/old app aliases and removes product
`*.open-iphone-use-backup` files in known client/skill locations; it is idempotent.

Uninstall restores/removes only unchanged MCP entries, never resurrecting the removed app.
Modified MCP entries are retained with a diagnostic. Managed skill aliases are removed and
an unchanged canonical skill is removed; the separate phone-state directory remains intact.
Registration manifests and config backups remain for owner recovery.

Restart detected clients after setup. If a client was installed later, re-run setup or run:

```sh
open-iphone-use register-agents
# To install a specific updated source:
open-iphone-use register-agents --skill /path/to/skill/SKILL.md
```

Reference schemas: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Cursor MCP](https://cursor.com/docs/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Release assets

Publishing a GitHub release runs the Release assets workflow, which uploads a source archive
and its SHA-256 file; it can also be run manually for an existing tag. Release creation and
publication are owner actions.
