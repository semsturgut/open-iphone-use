# Headless and multi-device validation — 2026-10-26

This record covers `feature/web-multi-device-crossplatform`, including the completion
checks after WIP commit `5d8528c`. Software checks, native radio capability and physical
phone outcomes are separate evidence.

| Check | Result on this Mac |
| --- | --- |
| Swift debug build | Passed |
| Swift tests | 60 passed, zero failures |
| Swift release build (app, legacy helpers, shared headless radio) | Passed |
| Safari capture-page check | Passed landing/board, audio keep-alive, background continuation, epochs, taps and stop checks |
| Legacy Swift MCP discovery/initialization | Passed; existing workflow/tool compatibility retained |
| Portable server tests | 25 passed, zero failures, including BlueZ contract tests with dbus-fast installed in the test environment |
| Local Python package build/install | Passed in the repository virtual environment; platform-neutral web assets packaged |
| Installed `serve_smoke.py` | Passed automatic CA/TLS, localhost operator UI/API, two independent synthetic capture enrollments/uploads and CLI device listing |
| Actual CoreBluetooth capability during serve smoke | Reported `peripheralUnsupported`, zero subscribed peers; physical HID was **not** verified |
| Legacy two-helper native smoke | Passed isolated JPEG/frame IDs, tap ingress, scoped stop, unbound-input rejection, restart and parent-exit cleanup; Bluetooth explicitly disabled for this separate check |
| POSIX installer | Earlier temporary-prefix install, rerun and uninstall passed; real home/client configurations were not used |
| `setup.sh` | ShellCheck and `sh -n` passed |
| `setup.ps1` | Official PowerShell parser and PSScriptAnalyzer warning/error checks passed on macOS; Windows execution remains untested |
| Web JavaScript / existing Mac build scripts | Node syntax and Bash syntax checks passed |
| Diff whitespace | Passed |

The Python tests exercise generated image-based two-click calibration, automatic association
of two synthetic phones, targeted keyboard reports, saved peer/calibration state, stale-frame
rejection, input release/cancellation, and cancellation of shared pairing probes. A stop or
stale candidate now aborts further probe movement and closes every association board.
They also cover role-separated tokens, independent invitation enrollment, atomic snapshots,
queue isolation, HTTP/TLS validation, CLI/MCP discovery and image responses, state leases,
CA reuse/leaf renewal, and client-registration rerun/uninstall preservation.

BlueZ tests instantiate the real D-Bus interfaces and exercise targeted-channel/shared-notify
routing with test objects. On macOS the channel test uses a connected datagram socket;
Linux uses SOCK_SEQPACKET. These checks do not establish that a particular BlueZ version
will invoke per-device AcquireNotify on real hardware. Windows notification tests execute
the actual notification method against test subscriber objects; they do not load WinRT or
exercise Windows permissions/drivers. Both product backends are implemented, not unavailable
placeholders, but still need host/hardware verification.

The installed CLI smoke used a real native helper without `OIU_DISABLE_BLUETOOTH`, without
an app bundle, and without a backend config/helper override. It created temporary credentials
and synthetic JPEGs only; no physical input reports were sent. The CoreBluetooth state above
prevents claiming physical control readiness in this execution context. The cause of that
unsupported result was not established; do not infer that the owner's Mac outside this
agent context necessarily has the same capability result. Check the startup radio status
and complete owner pairing/sharing in a normal Terminal before assessing real control.

SwiftPM required `--disable-sandbox --cache-path /tmp/oiu-swift-cache` because its nested
sandbox was blocked by the outer workspace environment. The outer sandbox remained enabled.
PowerShell was extracted to a temporary directory from an official, SHA-256-verified archive;
PSScriptAnalyzer was loaded from a temporary PowerShell Gallery package. No PowerShell
profile, system installation or real agent configuration was changed by these static checks.

## Reproduce

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./server
# Include BlueZ contract tests when developing on macOS:
.venv/bin/python -m pip install 'dbus-fast>=5.2,<6'
PYTHONPATH=server .venv/bin/python -m unittest discover -s server/tests -v
swift build --package-path macos
swift test --package-path macos
swift build --package-path macos -c release
cp macos/.build/release/open-iphone-use-headless .venv/bin/open-iphone-use-headless
.venv/bin/python server/tests/serve_smoke.py .venv/bin/open-iphone-use
PYTHONPATH=server .venv/bin/python server/tests/native_smoke.py macos/.build/release/open-iphone-use-headless
node macos/scripts/test-capture-page.mjs
python3 macos/scripts/test-agent-mcp.py macos/.build/release/open-iphone-use-mcp
shellcheck setup.sh
```

For PowerShell use its own parser and `Invoke-ScriptAnalyzer`; ShellCheck does not support
PowerShell. The installer test must use a temporary HOME, install root and bin directory.

## Still unverified

- Real iPhone Safari sharing, calibration and UI control through the new shared-radio path.
- Simultaneous physical phone control, connection capacity and reconnect behavior.
- Linux/BlueZ and Windows/WinRT execution, permissions, drivers and real notification delivery.
- Full Windows installer execution and Windows state ACL behavior.
- Native app WKWebView runtime behavior, full app/DMG packaging, clean-machine installation,
  signing/notarization, release delivery and internet deployment.
- Historical iOS project checks: this checkout has no `ios/` tree.

The earlier demo/device-matrix results describe the standalone Mac path. No push, release
or distribution-artifact update was performed for this work; `tools/` was left untouched.


## Bluetooth-first update

The old generic-enrollment and automatic screen-association checks above describe the
previous revision. The current flow requires an explicitly selected live Bluetooth peer;
`/join` and `/api/enroll` return 410. Current regression coverage includes two-phone
calibration in parallel with one blocked peer, Swift per-central FIFO/backpressure/read
state/cancellation, exact-identity BlueZ/WinRT name resolution, native full-UUID publication,
Bluetooth/sharing status, strict enrollment, CLI new/link/remove terminal QR, management
MCP schemas and stdio calls, and a real detached capture-only daemon's start/duplicate/stop
lifecycle. Capture-only mode cannot add a device.

Mac name retrieval tries exact-UUID cached peripheral/GAP lookup and marks a fallback when
unavailable. No personal device names/addresses were printed during schema inspection;
this sandbox's system_profiler call yielded no usable mapping. Real name lookup, paired
HID subscriptions, two physical iPhone taps and reconnect behavior remain unverified.
The simulator tests are not evidence of physical multi-phone radio capacity.
