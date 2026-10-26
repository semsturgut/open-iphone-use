# Web/headless preview validation — 2026-10-26

These checks apply to the source preview on `feature/web-multi-device-crossplatform`.
They do not establish physical iPhone behavior or release readiness.

| Check | Result |
| --- | --- |
| Swift debug build and tests | Passed, 60 tests, zero failures |
| Swift release build (app, original helpers, new headless helper) | Passed |
| Existing capture-page script | Passed landing/board, audio keep-alive, background continuation, epochs, taps and stop checks |
| Existing Swift MCP discovery/initialization script | Passed; standalone workflow/tool compatibility retained |
| Portable Python tests | Passed, 11 tests |
| Two real Swift headless helpers | Passed independent JPEG/frame IDs, taps, scoped stop, unbound input refusal, restart and parent-exit cleanup; Bluetooth disabled |
| Python package wheel build/install | Passed in a temporary virtual environment; packaged web assets available |
| Web JavaScript and Mac build-script syntax | Passed |
| Browser UI | Inspected in Chrome with synthetic screens; third phone appeared without reload; unavailable-HID error displayed honestly |
| Diff whitespace check | Passed |

The Python suite covers role-separated tokens, persistence/permissions, cross-device frame
rejection, stale-frame guards, independent queues, queued-action cancellation, capture
watchdog release, atomic screenshot metadata, JPEG/origin/route rejection, CLI lifecycle,
state-directory lease, MCP initialization/discovery/image/error responses, and trusted TLS
capture plus rejection of an untrusted certificate. The capture-page equality test protects
the packaged copy from drifting away from the native source.

SwiftPM needed `--disable-sandbox --cache-path /tmp/oiu-swift-cache` because the outer
workspace sandbox disallowed SwiftPM's nested sandbox. The workspace sandbox stayed enabled.
The Bluetooth purpose strings are embedded in the headless executable; privacy prompting
and Bluetooth permissions were not exercised.

## Not verified

- Real iPhone Safari sharing/calibration/control through the new server/helper path.
- Simultaneous pairing, advertising and HID routing on several physical iPhones/radios.
- Windows or Linux execution, radio capabilities, certificate provisioning or Windows ACLs.
- BlueZ/WinRT HID (explicit unavailable adapters only).
- Native app WKWebView runtime behavior, full app/DMG packaging, clean-machine installation,
  signing/notarization, release delivery or internet deployment.
- Historical iOS checks: this checkout has no `ios/` sources/project.

Existing release demonstrations in `device-matrix.md` describe the prior standalone app;
they are not evidence for headless/multi-phone behavior. No push, release or distribution
artifact update was performed.
