# Headless server and platform setup

The primary workflow is `setup.sh` / `setup.ps1` → `open-iphone-use serve` → browser grid.
The native Mac app is optional. The portable service implements HTTPS capture, credentials,
peer association, calibration, serialized input, CLI and stdio MCP. Each host has one native
radio service; no phone is selected through an implicit singleton or broadcast.

## Automatic startup

`serve` detects a private LAN IPv4 address, creates/reuses an owner-private CA and HTTPS
server certificate, starts the platform's HID peripheral, and prints owner setup instructions.
The defaults are:

| Listener | Purpose |
| --- | --- |
| `https://LAN_IP:27891` | Safari sharing and authenticated LAN web UI/API |
| `http://127.0.0.1:27893` | Authenticated operator grid/API; no desktop CA trust needed |
| `http://LAN_IP:27892/root-ca.cer` | Public CA certificate only; no control, screens or tokens |

Private operator links carry the login credential in the URL fragment, which is consumed
and removed by the web page. The generic private sharing invitation enrolls each browser
with its own ID/token. Safari localStorage retains that capture identity across visits;
private browsing, cleared storage or a changed server origin can create a new slot. An
inactive old slot's peer can be reassociated automatically; an active share's peer cannot
be silently stolen. Named links remain available with `add` or the UI's new-phone form.

Before trusting the CA on an iPhone, compare its SHA-256 fingerprint with the server's
terminal output, or transfer it over an already trusted channel. Install the profile and
enable trust explicitly in iPhone settings. The bootstrap HTTP endpoint transports only
public certificate bytes; it cannot establish authenticity by itself. Private keys are never
served. The localhost UI avoids a host-browser trust step; LAN operator browsers must trust
the CA too. IP changes renew the leaf certificate while preserving the trusted CA. CA
expiry/mismatched key material is reported rather than silently replacing the trust anchor.

Override addresses/ports only when needed:

```sh
open-iphone-use serve --host 192.168.1.20 --port 27891 --ui-port 27893 --certificate-port 27892
open-iphone-use serve --cert /private/server.pem --key /private/server.key
open-iphone-use serve --no-print-secrets      # log/service use; retrieve login with token
open-iphone-use --state /private/phone-state devices
```

`OIU_STATE` selects a custom state directory. `--url` overrides CLI discovery; by default
CLI/MCP read private `connection.json` and connect to the loopback API. Explicit custom
HTTPS endpoints use normal trust (`SSL_CERT_FILE` can supply a private CA); no TLS bypass
is provided. `--no-certificate-server` disables public certificate download. For local
software development only, `serve --local-http --capture-only` explicitly disables TLS
and HID; this is a diagnostic mode, not an automatically selected fallback.

## Pair and prepare

Enable AssistiveTouch and pair each phone to the host's advertised HID device through
owner-controlled settings. Keep the phone unlocked. Open the same private HTTPS invitation
in Safari on each phone, approve sharing, and keep Safari visible for initial preparation.
Tiles appear immediately for every registered phone, with a waiting placeholder until its
first upload; `devices` lists live, paused and enrolled slots.

The first tap on a tile automatically calls `ensure_session` to associate the screen
with an available Bluetooth peer. CLI/MCP can also request preparation explicitly. For one
new peer and one new capture the association is one-to-one. With several new peers it
opens the calibration boards and correlates targeted pointer movement with authenticated
Safari hover feedback or visible pointer changes in the corresponding captures. All new
candidate captures must show their boards. Ambiguity fails with actionable instructions;
it never chooses an arbitrary peer or sends a blind click. Re-pairing can be reassociated
without editing internal IDs. Pairing new phones one at a time is a recovery path when
hover feedback or a recognizable grey pointer is unavailable.

The common control engine measures two board clicks using the same fast16-sized motion
pattern as the legacy Mac workflow. It requires the green border, a visible white board,
a single grey pointer, fresh screenshots and matching tap feedback before saving the
mapping. Coordinates are converted from Safari viewport fractions to captured-screen
fractions using the measured border. Calibration is stored per device/peer, motion version
and aspect ratio. It is a two-point estimate, not a guarantee at every pixel. Screen
rotation, changed pointer settings or suspect accuracy require preparation again.

The grid offers live-screen taps, sharing-link controls and Remove. Home, typing,
scroll, explicit preparation and release input are available through CLI/MCP. On each action the server
checks a frame ID from that device within five seconds, serializes input, releases on
failure/cancellation, waits for a new frame and returns an image to MCP. Stop bypasses
queued work. Capture expiry releases held keys/buttons. Owner unlocking, certificate trust,
pairing, iOS sharing and protected-screen restrictions are preserved.

## Remove a phone

Confirm **Remove** next to a tile's Share/Copy/QR area, run
`open-iphone-use remove --device <id>`, or use the portable MCP `remove_device` tool
with an explicitly chosen `device_id`. The API is `DELETE /api/devices/<id>` with the
operator Bearer credential; a capture token or shared invitation cannot delete phones.

Removal persists revocation, cancels running and queued actions, drops in-memory frames,
detaches the phone's pairing/calibration engine and deletes its saved calibration. Old
capture links/uploads are rejected even after restart. Other phones, the shared radio,
the invitation for enrolling new phones and OS Bluetooth bonds remain intact. Removing
an unknown/already removed ID returns 404. A failed state write preserves the phone;
if radio cleanup fails after revocation, the response reports cleanup warnings while the
identity remains deleted. Removing a phone is separate from forgetting its Bluetooth
pairing in the owner's OS settings.

## macOS implementation

The native Swift helper runs with `--radio` and owns one CoreBluetooth peripheral manager,
one HID service and independent subscriptions/generations per central. Every update names
one concrete central. Per-peer release cancels its backpressured sends without releasing
other phones. The helper has embedded Bluetooth purpose strings and exits/releases input
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
read its screenshot, then prepare/act using its fresh frame ID. Stop requires only the
ID. The portable MCP surface covers keyboard, pointer, calibration, scroll, swipe/feed
and release, plus explicit phone removal. Advanced legacy diagnostic/retry aliases remain available only from the
standalone Swift helper. No model or API key is embedded in this project.

Each device stores its own capture credential, frame history, peer mapping/calibration,
action serialization and cancellation generation. State is owner-private (POSIX modes /
Windows NTFS ACLs), and an OS lease prevents two servers sharing the same state directory.
Frames are memory-only. Limits: 64 enrolled slots, 4 MiB per JPEG, 16 accepted actions per
device and 64 main HTTP connections. The server is for a trusted LAN/localhost. Token
rotation beyond per-device removal, high-density streaming optimization and internet-grade deployment
are separate future work. [Installation](installation.md), [validation](web-server-validation.md).
