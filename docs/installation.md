# CLI installation and removal

`setup.sh` supports macOS and Linux; `setup.ps1` supports Windows. Run the script from
this branch to install unmerged work. Remote `main` installers install the source on
`main`, not this feature branch. A downloaded installer can use `OIU_REF` to select an
archive ref or `OIU_SOURCE_DIR` to use an already reviewed checkout.

## macOS / Linux

```sh
sh setup.sh
~/.local/bin/open-iphone-use serve
```

The managed runtime is `~/.local/share/open-iphone-use/runtime`, the launcher is
`~/.local/bin/open-iphone-use`, and device credentials/CA/calibration stay separately in
`~/.open-iphone-use`. Add `~/.local/bin` to PATH if your shell does not already include it.
No shell profile is silently rewritten. Existing legacy app CLI symlinks are migrated and
restored on uninstall; unrelated occupied CLI paths are refused.

Setup uses Python 3.11+ if present. Otherwise it bootstraps Astral's official `uv` installer
in the managed runtime and installs a private CPython 3.12, without changing the system
Python. Dependencies come from PyPI. On macOS it builds `open-iphone-use-headless` from
source using Swift 6; installing Xcode/accepting its license remains an owner action. On
Linux it installs missing BlueZ/D-Bus using apt, dnf or pacman with sudo, and starts the
Bluetooth service where systemd is available. OS package-manager permissions may prompt.
Unsupported distributions receive an explicit dependency instruction. Run the server as
your normal user, not root.

Options and overrides:

```sh
sh setup.sh --no-agents                  # runtime/CLI only
sh setup.sh --uninstall                  # keep phone state and certificates
OIU_INSTALL_ROOT=/private/path/runtime OIU_BIN_DIR=/private/path/bin sh setup.sh
OIU_PYTHON=/path/to/python3 sh setup.sh
```

`OIU_INSTALL_ROOT` and `OIU_BIN_DIR` must be absolute. Managed directories are marked;
unmanaged directories and unrelated CLI files are preserved. Do not store user files
inside the managed runtime. Stop an active server before upgrading. Re-running setup
uses the same installation and client entries. `OIU_SWIFT_DISABLE_SANDBOX=1` is an optional
SwiftPM-only workaround for environments whose outer sandbox blocks nested sandboxing;
it does not change OS Bluetooth permissions or the outer sandbox.

## Windows

```powershell
.\setup.ps1
open-iphone-use serve
.\setup.ps1 -Uninstall
```

If CPython 3.12 is missing, setup uses user-scoped `winget` installation, or tells the owner
to install it when winget is unavailable. Python native WinRT wheels supply the GATT
implementation; no custom driver, DMG or C# build is required. The managed runtime and
native console launcher live under `%LOCALAPPDATA%\OpenIPhoneUse`. Setup adds its `bin`
to the user PATH and tracks ownership so uninstall removes only that entry. A copied pip
console launcher preserves Unicode paths and avoids changing PowerShell execution policy.
If your execution policy disallows the downloaded setup script, review it and use your
organization's permitted invocation; setup never bypasses policy.

`-NoAgents`, `-InstallRoot`, `-SourceDir` and `-Ref` customize installation. Bluetooth
privacy/driver/policy checks happen when the server starts; enable Windows Firewall access
on the private network if prompted. Device state is owner-private using an NTFS ACL with
the current user and SYSTEM; FAT/exotic filesystem/ACL failures are reported.

## Agent registration and Skill

Detected Codex, Claude Code and Cursor installations receive only an `open-iphone-use`
stdio MCP entry. Codex uses `~/.codex/config.toml`; Claude Code uses `~/.claude.json`;
Cursor uses `~/.cursor/mcp.json`. Existing entries, unrelated settings and approval
policies are retained. Replacing this product's previous entry creates an owner-private
backup and records the previous value. Syntax errors or symlinked configs are refused
rather than overwritten. The shared Skill is installed in `~/.agents/skills`; Claude also
receives its native `~/.claude/skills` copy when detected.

Uninstall restores/removes only entries and Skill files still matching what setup wrote.
User modifications are left in place with a diagnostic. Full backups and the registration
manifest remain under `~/.local/share/open-iphone-use/registrations`. They can contain
private client settings; do not publish them. Runtime removal preserves the separate
phone-state directory and CA so a later reinstall does not force certificate re-trust.
Uninstalling Linux system packages, revoking OS pairings or deleting the CA is deliberately
an explicit owner/system action rather than part of removing this user's CLI.

Restart detected clients after setup. If a client was installed later, re-run setup or run:

```sh
open-iphone-use register-agents --skill /path/to/macos/Resources/open-iphone-use-skill/SKILL.md
```

Reference schemas: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Cursor MCP](https://cursor.com/docs/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp).


After installation, `open-iphone-use serve` detaches by default and prints a short status,
the loopback Web UI and private log path. Open that Web UI directly, without a token.
Use `serve --foreground` to debug Bluetooth permissions and `stop` to shut down. Pair via
AssistiveTouch, use `new` (or select a Bluetooth peer in the UI), scan that phone's QR,
then approve Safari sharing. `devices`, `status --device`, `link` and `remove` work without
a host browser. Distribution builds use these setup scripts and the CLI only.
