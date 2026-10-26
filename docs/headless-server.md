# Headless, web and multi-device server (source preview)

The portable server runs without `Open iPhone Use.app`. Python 3.11+ owns HTTP/TLS,
credentials, screen sessions, device routing, action queues, web assets and MCP. Python's
standard library was chosen to run unchanged on macOS, Linux and Windows without a
native Swift runtime or Node dependency. Swift remains the macOS HID/calibration adapter.
No new AI/model service or cloud account is required.

This is a trusted-local-network development server, not an internet-facing deployment.
The standard-library HTTP server has limited hardening ([Python documentation](https://docs.python.org/3/library/http.server.html)).
There are no Linux/Windows binary packages or verified physical multi-iPhone results yet.

## Run from source

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./server
.venv/bin/open-iphone-use serve
# Another terminal: retrieve the private operator credential, then paste it into the UI.
.venv/bin/open-iphone-use token
```

Open `http://127.0.0.1:27891` in a browser. On Windows use `py -3 -m venv .venv` and
`.venv\Scripts\open-iphone-use.exe`. The server starts with capture/viewing enabled and
HID unavailable until a backend is configured. The default state is `~/.open-iphone-use`;
use global `--state /short/private/path` or `OIU_STATE` to change it. Do not put state in Git.
The virtual environment's CLI and the old DMG CLI share a name: use the explicit path.
The Swift CLI also forwards `serve`, `devices`, `add`, `action`, `token`, `status --device`
and `mcp --portable` to Python; set `OIU_SERVER_PYTHON` to the installed environment's Python.

For iPhones on the LAN, supply a PEM certificate/key for the host/IP and bind to its
LAN address. The owner must install/trust the issuing CA on each phone, using a trusted
channel, and keep the private key on the server:

```sh
.venv/bin/open-iphone-use serve --host 192.168.1.20 --cert /private/server.pem --key /private/server.key
.venv/bin/open-iphone-use --url https://192.168.1.20:27891 devices
```

Certificates are provided through `ssl.SSLContext`; no platform-specific certificate
creation/trust mutation is performed. LAN plaintext listeners are refused. Python CLI
clients use normal system/OpenSSL trust; for a private CA set `SSL_CERT_FILE` to its
public PEM. There is deliberately no insecure TLS bypass. Do not use `0.0.0.0` in links;
select the host's reachable LAN address. Localhost HTTP is for local development only;
a physical iPhone needs trusted HTTPS and an owner-approved Safari sharing session.

In the UI, create a separate named sharing link for each iPhone. Send each link privately
to its intended phone and approve sharing there. On the first upload its tile appears
automatically, with live/paused state, frame age, preview and controls. Multiple screens
are polled side by side; only the newest JPEG per phone is stored in memory. A link is a
bearer credential: opening the *same* link on two phones writes to the same device slot.
There is no untrusted automatic hardware identity detection from a browser. Separate links
are the explicit enrollment boundary. Registry entries persist; screens do not survive restart.

```sh
open-iphone-use devices
open-iphone-use add "Test iPhone"  # Prints this phone's private capture URL
open-iphone-use status --device DEVICE_ID
open-iphone-use action --device DEVICE_ID send_key \
  --arguments '{"expectedFrameID":"FRESH_FRAME_ID","key":"cmd+h"}'
open-iphone-use action --device DEVICE_ID stop_control
```

Use `--url` before the subcommand for a nondefault server. `devices` includes enrolled
phones with paused/not-yet-sharing status, so an absent live tile can still be diagnosed.
Every input command, including preparation, requires a current capture frame ID from that
same device. Release input bypasses the queue and requires no frame. Browser buttons and
MCP supply IDs; CLI callers can read `/api/devices/ID/snapshot` with operator authorization.
Unsupported HID returns an explicit failure while viewing remains available.

## macOS HID without the app

```sh
swift build --package-path macos -c release
```

Create devices first, then stop the server and create a private JSON backend config:

```json
{
  "DEVICE_ID": {
    "kind": "macos",
    "executable": "/absolute/repo/macos/.build/release/open-iphone-use-headless"
  }
}
```

Restart with `serve --backend-config /private/backends.json`. Each device gets a separate
helper, same-user Unix socket, frame store, calibration namespace and action controller.
The advertised name is `OIU ` plus the first eight characters of the device ID. Pair the
intended phone through owner-controlled Bluetooth settings and enable AssistiveTouch.
`status --device ID` exposes the observed `backend.status.centralIdentifier`. Confirm the
phone and add `"central": "OBSERVED_UUID"` to that device's config, then restart. Without
this explicit binding, the helper can receive frames and report pairing state but refuses
input. Duplicate central bindings are rejected. Subscriptions from a different central
cannot receive HID reports after binding. Use Prepare with Safari visible; the original
two-click calibration and board feedback work through the headless frame/tap channel.

The existing CoreBluetooth backend remains one central per helper. Several helper sessions
can be configured, but simultaneous advertising/pairing/routing through one physical Mac
radio has **not been demonstrated**. OS radio limits may prevent it. Multi-device core and
capture support are implemented; reliable many-phone physical control is a remaining
hardware-validation milestone, not a proven feature. Do not run legacy standalone mode
and headless helpers against the same paired phone.

The native app is an optional client: launch its executable with
`OIU_SERVER_URL=https://HOST:27891` (or localhost HTTP). It shows the same grid in WKWebView
and starts no local controller, radio, socket or receiver. Trust must already be configured;
the client does not override certificate errors. With no environment variable the app
keeps its original single-phone standalone behavior. Existing app/DMG scripts are preserved;
they do not bundle/install Python or the new helper yet.

## MCP

Register the portable environment's executable with `args: ["mcp"]`. For a custom state
or server, global arguments precede `mcp`:

```json
{
  "mcpServers": {
    "open-iphone-use": {
      "command": "/absolute/repo/.venv/bin/open-iphone-use",
      "args": ["--url", "https://HOST:27891", "mcp"]
    }
  }
}
```

MCP is stdio JSON-RPC forwarding to the running server, not a publicly exposed MCP listener.
It supports initialization, ping, tools discovery/calls and image content. Every device
operation requires `device_id`; `list_devices` returns explicit IDs. Input tools also require
`expected_frame_id`. The original Swift MCP helper remains for standalone-app compatibility.
The portable tool surface covers preparation, relative/absolute pointer, clicks, keyboard,
scroll, swipe/feed and stop. Advanced legacy diagnostic/retry aliases are not exposed yet.
After input, inspect a fresh screenshot; neither transmission nor changing pixels certify success.

## Adapter boundaries and platform status

| Piece | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Registry, queues, CLI, web, HTTP/TLS, stdio MCP | Implemented/tested here | Portable implementation; host testing pending | Portable implementation; host testing pending |
| Capture | Existing Safari page reused; synthetic uploads tested | Same HTTP transport; Safari on iPhone still required | Same HTTP transport; Safari on iPhone still required |
| HID | Native CoreBluetooth helper builds; physical headless behavior untested | `BlueZBackend` explicit unavailable stub | `WindowsBackend` explicit unavailable stub |
| Certificates | Explicit PEM + owner trust; original app CA preserved | PEM adapter; provisioning planned | PEM adapter; provisioning planned |

`HIDBackend.request/ingest/stop_capture/close` is the device-scoped boundary; capture transport is in `http.py`,
state/actions in `core.py`, TLS in `tls_context`. Backend configuration is local-only and
cannot be changed through the web API. A native adapter receives logical commands, frame
updates and tap feedback, and must serialize input, bind its central, stop on stale capture,
release on stop/cancellation/disconnect, and return real failures. It must not silently
broadcast commands to all subscribers.

Linux plan: implement a D-Bus service using BlueZ `GattManager1.RegisterApplication` and
`LEAdvertisingManager1.RegisterAdvertisement`, encrypted keyboard/mouse report attributes,
report-reference descriptors and per-central subscription/notification routing; integrate
BlueZ owner pairing/authorization agents and adapter selection. Verify hardware peripheral
support and simultaneous centrals before promising support. Relevant upstream interfaces:
[BlueZ GATT API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.GattManager.rst),
[advertising API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.LEAdvertisingManager.rst).
Do not substitute a BLE *central* library for an HID peripheral implementation.

Windows plan: a C#/WinRT sidecar using `GattServiceProvider`, encrypted report characteristics,
notifications and device binding. First probe `BluetoothAdapter.IsPeripheralRoleSupported`;
check HID service restrictions, required Bluetooth capabilities, adapter/driver behavior,
and background process lifecycle on actual Windows hardware. Microsoft's
[GATT server sample](https://github.com/microsoft/Windows-universal-samples/tree/main/Samples/BluetoothLE)
is feasibility evidence for GATT, not proof of iPhone HID support. Both adapters currently
fail closed and can be selected with `kind: bluez` / `kind: windows` to diagnose availability.

## Security and limits

The state directory is owner-private and the JSON credential file is mode 0600 on POSIX;
Windows ACL inheritance must be reviewed before deployment. A single-writer OS lock prevents
two servers using the same directory. The operator token grants viewing/control of all phones;
each capture token can only upload frames/taps and stop its own stream. There is no cross-origin
API access. Credentials are not included in request logs, HTML, device lists or screenshots.
The login token remains in browser sessionStorage; clear site data to remove saved access.
Sharing links are sensitive and retained in Safari history. Credential rotation/revocation,
audit logging, Windows ACL tooling and internet-grade authentication remain planned.

Frames are bounded to 4 MiB, device enrollment to 64, pending actions to 16 per device and
HTTP connections to 64 with read timeouts. Queues are isolated per device; release input
invalidates queued work and bypasses an active operation. The watchdog releases input when
capture expires after five seconds. Native actions preserve the original fresh-frame and
post-action checks. Keep iPhones unlocked; no automatic pairing/trust/unlock is provided.
Web polling/base64 snapshots favor portability over high-frame-rate efficiency. At many
phones, bandwidth and per-upload native IPC need profiling; WebSocket/MJPEG transport and
bounded native ingestion latency are future work.

## Checks

See [the source-preview validation record](web-server-validation.md) for tested and untested behavior.

```sh
PYTHONPATH=server python3 -m unittest discover -s server/tests -v
swift test --package-path macos
swift build --package-path macos -c release
node macos/scripts/test-capture-page.mjs
python3 macos/scripts/test-agent-mcp.py macos/.build/release/open-iphone-use-mcp
```

In restricted environments SwiftPM may need `--disable-sandbox --cache-path /tmp/oiu-swift-cache`;
this does not disable the outer workspace sandbox. No `ios/` tree exists in this checkout,
so the older CONTRIBUTING iOS commands cannot be run here. Tests/builds, synthetic transport,
UI inspection and actual iPhone control must be reported separately.
