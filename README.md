# Open iPhone Use

Control your own iPhones through a local CLI server, Bluetooth HID, Safari screen sharing,
a browser grid and MCP. macOS is the primary platform. Linux uses real BlueZ D-Bus APIs;
Windows uses real WinRT GATT APIs. No model or AI API key is embedded in the server.

## Quick start

From this source checkout on macOS or Linux:

```sh
sh setup.sh
open-iphone-use serve
```

On Windows:

```powershell
.\setup.ps1
open-iphone-use serve
```

Setup installs an isolated Python runtime, the native macOS HID helper when applicable,
and MCP registration for detected Codex, Claude Code, Cursor, OpenCode and Command Code clients.
The canonical skill lives only at `~/.agents/skills/open-iphone-use/SKILL.md`; client
skill directories use relative aliases (Windows junction/copy fallback).
macOS needs Xcode/Swift 6; Linux needs BlueZ/D-Bus; Windows needs a peripheral-capable
Bluetooth driver. Follow owner-controlled Bluetooth permission and pairing prompts.
Use the checked-out installer for this development branch; remote `main` may be older.

`serve` starts in the **background by default**, survives closing the launching terminal,
and prints only the local Web UI, Bluetooth state, log path and useful commands:

```text
Open iPhone Use is running
Web UI:  http://127.0.0.1:27893
Bluetooth: ● on  (advertising)
Add a phone: open-iphone-use new
Stop:      open-iphone-use stop
Log:       /.../.open-iphone-use/server.log
```

Open the Web UI directly. **No token or login link is needed.** Operator UI/API access is
restricted to the loopback listener and loopback clients, with Host/Origin checks; the
LAN HTTPS capture listener cannot serve operator requests. Any local process with access
to the loopback port can operate the server. Do not expose that port through a proxy.
Use `open-iphone-use serve --foreground` for terminal debugging. Background execution is
not an automatic login/reboot service. Logs are private (0600) in the state directory.

## Pair, add and share each phone

1. On the unlocked iPhone enable AssistiveTouch. Open **Settings → Accessibility → Touch →
   AssistiveTouch → Devices → Bluetooth Devices → Open iPhone Use** and approve pairing.
   Windows may advertise the PC name. Both keyboard and pointer HID subscriptions must
   be live; a remembered Bluetooth bond alone is insufficient. On headless Linux, keep
   an owner-operated `bluetoothctl` session with `agent KeyboardDisplay` and
   `default-agent` for pairing-code confirmation; the detached server cannot approve it.
2. Select that phone from **Bluetooth-connected phones** in the Web UI, or run:

   ```sh
   open-iphone-use bluetooth
   open-iphone-use new                  # selects one available phone, otherwise prompts
   open-iphone-use new --bt PEER_ID      # explicit choice, required for ambiguous scripts
   ```

   Each record is permanently bound to the selected Bluetooth peer and gets its own
   private sharing URL and QR. Names come from Bluetooth. Generic/unavailable names fall
   back to `iPhone`; duplicate names get a short peer-ID suffix. Never infer identity from
   a name. If needed, pair/add one phone at a time to identify its QR.
3. On first use install the public CA on the phone, verify its SHA-256 fingerprint against
   the terminal, then enable it in **Settings → General → About → Certificate Trust
   Settings**. `new`/`link` show the CA address and fingerprint; only public certificate
   bytes are downloadable. No desktop certificate trust is needed for the loopback UI.
4. Scan the phone's QR **on that same phone**, open Safari and approve **Start sharing**.
   Each added phone is immediately visible in the side-by-side grid, with a waiting
   placeholder until frames arrive. Share/AirDrop (when supported), Copy and QR persist.
5. Tap a live tile. Its first tap prepares the pointer with Safari visible. Calibration
   tolerates lagging frames: it waits up to about three seconds for one pointer to be
   stable in two fresh frames at a plausible position before clicking. Inspect each result.

For **two iPhones**, repeat the sequence for each. Bindings, frames, calibration, input
queues, report state and cancellation are per phone. One phone's calibration waits or
backpressure do not hold a global action lock. CoreBluetooth notifications name a specific
central; BlueZ/WinRT also target a specific peer. Older BlueZ shared-notify implementations
require one phone per adapter and reject ambiguous broadcasts.

Each tile and the global header show separate Bluetooth and Screen share lights. Red
means **control won't work**. CLI and MCP expose the same booleans and status messages.
Both Bluetooth and fresh screen sharing are required; do not switch to another phone
when the intended one disconnects.

## CLI, MCP and skill

```sh
open-iphone-use devices
open-iphone-use status --device DEVICE_ID
open-iphone-use link DEVICE_ID           # re-show private URL and width-aware terminal QR
open-iphone-use remove DEVICE_ID         # also supports remove --device DEVICE_ID
open-iphone-use action --device DEVICE_ID ensure_session --arguments '{}'
open-iphone-use action --device DEVICE_ID pointer_tap \
  --arguments '{"expectedFrameID":"RECENT_FRAME_ID","x":0.5,"y":0.5}'
open-iphone-use action --device DEVICE_ID stop_control
open-iphone-use stop
```

The server must remain running for links to work; device links survive restart. A terminal
QR uses black-on-white Unicode half-blocks with a quiet zone and adapts to column width.
If it cannot fit without wrapping, the CLI prints a warning and the link alone.

The grid keeps screen taps, sharing controls and Remove. Keyboard, Home, typing, scroll
and explicit release/preparation are available through CLI/MCP. Remove asks for confirmation
in the UI, cancels running/queued input, clears frames and calibration, and revokes the
capture token permanently. Other phones and OS Bluetooth bonds remain intact.

Setup registers `open-iphone-use mcp` as a stdio server and installs the bundled
[Open iPhone Use skill](macos/Resources/open-iphone-use-skill/SKILL.md). Restart your MCP
client after setup. Run `open-iphone-use register-agents` to register clients installed later.
OpenCode JSON/JSONC comments and unrelated settings are retained; private backups precede
config edits. Old application entries, duplicate skills and product backup files are cleaned.
MCP stays available when the web server is down: tools return
“Server not running — start it with: open-iphone-use serve”; start the service and retry
without restarting the MCP client. Diagnostics go to `~/.open-iphone-use/mcp.log` (or
`--state`/`OIU_STATE`), never protocol stdout. EOF/closed pipes disconnect cleanly.
Use `list_bluetooth_candidates`/`create_device` for requested setup;
then `list_devices`, select an explicit `device_id`, inspect its screenshot and act.
`ensure_session` and `prepare_session` obtain their own fresh frames and **do not require
expected_frame_id**. Other input accepts an ID from that phone's last approximately two
seconds of frames, not only the latest frame. Old, foreign or stopped-stream IDs fail.
Inspect the returned image after each action. HID acceptance alone is not UI success.

After a later server restart, the helper publishes the same HID service and advertises
again for bonded phones; macOS also restores CoreBluetooth services/subscriptions when
available. Keep using the same installation and state directory. iOS owns reconnection:
if a phone stays red, unlock it and select the existing **Open iPhone Use** entry under
AssistiveTouch > Devices > Bluetooth Devices. Re-pair only if the OS lost its bond.
Automatic reconnect timing, driver limits and OS-managed GATT caching still need hardware
verification. Bluetooth reconnection does not resume Safari screen sharing; approve it again.
See [platform reconnect details](docs/headless-server.md#bluetooth-reconnection).

## Network and safety

Keep the host and phones on a trusted private network. Auto LAN mode refreshes sharing
links/certificates when the network changes; open a new link and reapprove sharing on the
phone. Explicit `--host` and custom `--cert`/`--key` configurations stay fixed. Operator
access always stays loopback-only. Capture links are credentials; never publish them.
Only the owner can pair, trust certificates, unlock and approve iOS sharing.

Text uses the US ASCII layout. Protected content, locking and iOS permission boundaries
remain in force. Radio limits, real Bluetooth names, two-phone input/reconnection and
Linux/Windows behavior need hardware checks; automated transport/image tests are separate
from physical proof. The owner's live diagnosis established delayed pointer rendering;
the latency fixes still need a repeat physical run. See [validation](docs/web-server-validation.md),
[server details](docs/headless-server.md), [installation](docs/installation.md) and
[contributing](CONTRIBUTING.md). Distribution and release builds use the CLI/setup workflow.
