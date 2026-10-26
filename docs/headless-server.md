# Headless server and platform setup

The primary workflow is `setup.sh` / `setup.ps1` → `open-iphone-use serve` → browser grid.
The host is operated through the CLI and loopback Web UI. The portable service implements HTTPS capture, credentials,
peer association, calibration, serialized input, CLI and stdio MCP. Each host has one native
radio service; no phone is selected through an implicit singleton or broadcast.

## Automatic startup

`serve` detects a private LAN IPv4 address, creates/reuses an owner-private CA and HTTPS
server certificate, starts the platform's HID peripheral, and prints owner setup instructions.
The defaults are:

| Listener | Purpose |
| --- | --- |
| `https://LAN_IP:27891` | Safari sharing/capture API only |
| `http://127.0.0.1:27893` | Loopback-only operator grid/API; no token or desktop CA trust needed |
| `http://LAN_IP:27892/root-ca.cer` | Public CA certificate only; no control, screens or tokens |

## Bluetooth-first onboarding, including two phones

Run setup, then `open-iphone-use serve` (background by default), or `serve --foreground` for debugging. Pair each unlocked iPhone
in AssistiveTouch → Devices → Bluetooth Devices. The server lists only live HID peers
with both keyboard and mouse subscriptions as candidates. A remembered bond alone is
insufficient. Use `open-iphone-use bluetooth`, then `new --bt <id>`; `new` chooses the
only available phone automatically, or prompts on an interactive terminal when several
are available. Noninteractive callers must specify `--bt` when the choice is ambiguous.
The web UI offers the same Bluetooth-connected list; free-text names are not accepted.

The device is persisted with that exact `bt_id`, the Bluetooth name (or a marked fallback),
a random capture credential and a private URL. `new` renders a QR code using the vendored
MIT Project Nayuki encoder, with no new runtime dependency. Scan it on that same iPhone.
`link <device>` reprints the URL/QR after terminal closure or server restart. Install the
public CA on the phone once, compare the printed SHA-256 fingerprint, and explicitly
approve full trust before Safari sharing. Only the public certificate is downloadable;
private keys and capture credentials stay private. The loopback Web UI opens without a token; operator routes reject non-loopback
clients, non-loopback Host names and cross-origin browser requests. LAN capture cannot
serve the operator UI/API.

Repeat this sequence for two iPhones. Devices never change peers because another phone
connected or a share stopped. There is no screen-based guessing or global preparation
lock. Each phone has its own calibration board, reports, input lock, frame stream and
cancellation generation. The first web tap calls `ensure_session` for that phone only;
CLI/MCP can request preparation explicitly. The measured two-click pointer calibration
still requires Safari's green board, a visible pointer and authenticated tap feedback.
A disconnected peer is reported separately from a paused screen; actions require both
live HID and fresh capture. Re-pairing with a different OS identifier requires remove/new.

Names on macOS use exact-UUID CoreBluetooth peripheral retrieval, then a bounded reverse
connection/GAP Device Name (2A00) read when a matching peripheral is available. CBCentral
itself has no name or public address. IOBluetooth classic-device addresses and profiler
names cannot safely be matched to an opaque central UUID, especially with two iPhones;
these are not guessed. iOS need not expose a reverse-role GAP service. Generic cached names do not skip GAP lookup. The fallback
`iPhone` is marked `name_source: fallback`; duplicate labels include a short peer-ID suffix. Pair/add one phone at a
time if fallback labels cannot be identified. Linux uses Device1 Name/Alias; Windows
resolves BluetoothLEDevice.Name from each exact subscribed-client device ID.

## Background server

```sh
open-iphone-use serve
open-iphone-use bluetooth
open-iphone-use new                  # interactive pick, or automatic if exactly one
open-iphone-use new --bt PEER_ID
open-iphone-use devices              # JSON: names, bt_id, bluetooth_connected, sharing_active
open-iphone-use link DEVICE_ID
open-iphone-use remove DEVICE_ID
open-iphone-use stop
```

`serve` detaches on POSIX/Windows, retains the state lease, and writes a 0600 private
`server.log` without printing sharing/operator credentials into that log. Startup waits
for a matching authenticated instance identity; duplicate starts fail. `stop` uses the
loopback-only shutdown endpoint, never a potentially reused PID from disk.
This is persistent background execution until stopped/logout/reboot, not an installed
launchd/systemd/Windows startup job. Run `serve --foreground` if host Bluetooth permission
needs an owner prompt. On headless Linux the pairing agent still needs the interactive
owner terminal or the system Bluetooth UI; daemonization never auto-approves pairing.

`OIU_STATE` selects the state directory. CLI/MCP discover loopback `connection.json`;
`--url` overrides it. Auto LAN mode binds capture and CA-download listeners to all IPv4
interfaces. The UI/setup endpoint checks the current LAN IP, reloads a renewed leaf under
the same CA and updates links. Reopen a changed link on each iPhone. Explicit `--host`
and custom `--cert`/`--key` stay fixed. `--local-http --capture-only` is diagnostic only:
it offers no Bluetooth candidates and cannot add phones.

## Remove a phone and API

Confirm Remove next to a tile's Share/Copy/QR area, run `remove <device>` (or
`remove --device <id>`), or use MCP `remove_device`. Revocation is persisted, running/queued
input canceled, frames cleared, control detached and its calibration deleted. Old tokens
fail even after restart; other devices and OS Bluetooth bonds remain intact. Failed state
writes preserve the record; cleanup errors after revocation are returned as warnings.
A peer being removed cannot be re-added until its cleanup has drained.

Loopback-only operator endpoints (no bearer credential):

- `GET /api/bluetooth`: connected candidates, `id`, name/source, reports, availability.
- `POST /api/devices` with `{"bt_id":"..."}`: create only for that connected, unclaimed peer.
- `GET /api/devices`: names, Bluetooth/sharing status, exact binding and private capture path.
- `DELETE /api/devices/<id>`: remove/revoke.
- `GET /api/setup`: current capture origin and public CA metadata.
- `GET /api/health`, `POST /api/shutdown`: loopback service lifecycle.

Legacy `/join` and `/api/enroll` return 410: an invitation can no longer create unbound
phones. Existing records with an exact saved calibration peer migrate to that binding;
ambiguous/unbound records remain visible but cannot control a phone. Remove/new those
records. Persisted bindings are never reassigned by names or screen activity.

## macOS implementation

The native Swift helper runs with `--radio` and owns one CoreBluetooth peripheral manager,
one HID service and independent subscriptions, FIFO queues and last-report state per central. Every update names
one concrete central. Readiness callbacks retry each central independently; blocked peers do not gate others.
Per-peer release cancels only that phone’s backpressured sends. The helper has embedded Bluetooth purpose strings and exits/releases input
when its parent server exits. Python supplies capture/calibration and the web UI; no
SwiftUI lifecycle or app bundle is needed. Existing standalone app/controller/MCP behavior
is preserved separately.

Setup builds and places the helper next to the runtime. A source environment can set
`OIU_MAC_HELPER` or use `serve --helper PATH`; source checkouts are also discovered and can
build it when missing. These are overrides, not normal setup requirements. Approve the
Bluetooth privacy prompt, enable the radio, and close the legacy standalone app to avoid
competing services. Initialization/permission/backpressure failures are reported. A restricted
agent sandbox can prevent Bluetooth XPC even when compilation and the web server work;
run the owner CLI normally when that diagnostic appears.

## Linux implementation — implemented, not yet hardware-verified

The real BlueZ service uses `dbus-fast` on the system bus. It registers an ObjectManager
with HID service, encrypted report map/information/protocol/control characteristics,
keyboard/mouse report values, report-reference descriptors and a connectable LE advertisement.
Every capable/powered adapter is registered. The terminal pairing agent implements PIN,
passkey and numeric-comparison flows and requires owner input; no auto-approval or automatic
trust is used. If default-agent access is denied, authorize through the system Bluetooth UI.

Per-peer `AcquireNotify` channels use SOCK_SEQPACKET and SCM_RIGHTS FD passing. If the
installed BlueZ uses shared `StartNotify`/Value notifications instead, the code permits a
send only when a single paired HID central is connected on that adapter and no conflicting
acquired channels exist. Ambiguous shared notifications are refused. On those versions,
use one phone per BLE-capable adapter for simultaneous control. Unrelated connected
headsets/peripherals are excluded from HID peer candidates by tracking HID attribute reads.
Radio/driver connection and advertisement limits still apply. This path needs verification
on actual BlueZ/iPhone hardware; software tests do not prove stack-level FD invocation.

Install/start `bluez` and `dbus`, enable the adapter, and run as a user authorized to call
`org.bluez` (desktop Bluetooth/polkit policy). Setup handles common package managers;
permission/policy failures include commands/settings to inspect. Do not run the whole web
server as root to work around a missing policy. Relevant upstream interfaces:
[BlueZ GATT](https://github.com/bluez/bluez/blob/master/doc/org.bluez.GattCharacteristic.rst),
[GATT registration](https://github.com/bluez/bluez/blob/master/doc/org.bluez.GattManager.rst).

## Windows implementation — implemented, not yet hardware-verified

The real WinRT service uses native PyWinRT v4 projections in an owned MTA event loop.
It probes `BluetoothAdapter.IsPeripheralRoleSupported`, creates the HID service, encrypted
read/write/report characteristics, report-reference descriptors, deferred read/write
handlers and connectable advertising. It tracks client sessions and uses only the targeted
`NotifyValueAsync(buffer, subscribedClient)` overload, checking notification size and result
status. Shutdown removes event handlers and stops advertising. The same common calibration
and per-phone control engine is used on Windows.

Requires Windows 10 1703+ or Windows 11 with a compatible driver; OS Bluetooth privacy,
enterprise policy, GATT service restrictions and firewall rules can prevent operation.
Errors include the failed operation/status and setup guidance; unsupported hardware is
reported as failure, never simulated success. Packaged deployments must declare Bluetooth
capability; the installer supplies a desktop CLI, not a signed MSIX. Actual driver/privacy/
GATT behavior still needs Windows hardware validation. Microsoft's
[GATT server guide](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/gatt-server)
and [targeted notification API](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattlocalcharacteristic.notifyvalueasync)
document the underlying interfaces, not verified iPhone compatibility.

## MCP and limits

Register the installed CLI with `args: ["mcp"]`. List devices, choose an explicit ID,
read its screenshot, then act using an ID from that device’s last two seconds of
frames. Preparation and Stop require only the device ID; preparation captures its own
observations. The portable MCP surface covers keyboard, pointer, calibration, scroll, swipe/feed
and release, plus list_bluetooth_candidates/create_device/remove_device. Advanced legacy diagnostic/retry aliases remain available only from the
standalone Swift helper. No model or API key is embedded in this project.

Each device stores its own capture credential, frame history, peer mapping/calibration,
action serialization and cancellation generation. State is owner-private (POSIX modes /
Windows NTFS ACLs), and an OS lease prevents two servers sharing the same state directory.
Frames are memory-only. Limits: 64 enrolled slots, 4 MiB per JPEG, 16 accepted actions per
device and 64 main HTTP connections. The server is for a trusted LAN/localhost. Token
rotation beyond per-device removal, high-density streaming optimization and internet-grade deployment
are separate future work. [Installation](installation.md), [validation](web-server-validation.md).


## Delayed-frame safety and live status

Calibration and coordinate taps wait up to roughly three seconds for exactly one grey
pointer to be stable in two different fresh frames, at a plausible commanded position.
Home sends 20 reports on each axis before moving into the board. Board opening also
requires two stable observations, and delayed tap feedback is matched to the actual press
and pointer position. A green status dot alone is not a calibration board. No global
lock is held across these waits. Preparation has no caller frame-ID requirement; other
input validates the recent per-device ring and still rejects stale/foreign IDs.

`set_page_board(aim=true)` waits for the board to be visible, rather than acknowledging
only a flag change. Both that command and `pointer_home` reject an unsubscribed peer.
Hardware acceptance and visual UI success remain separate evidence.

`bluetooth_connected`, `sharing_active`, `bluetooth_status` and `screen_share_status`
are exposed in devices/status and MCP. Global/tile lights are green when connected and
red otherwise, explicitly stating that control will not work. `/api/status` provides
global status to the loopback UI. The capture listener accepts only scoped capture
requests, even from a local client; it never authorizes operator actions with a token.
