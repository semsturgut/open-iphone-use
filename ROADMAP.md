# Open iPhone Use roadmap

The source tree now includes a portable Python headless server and optional native Mac
client and real platform HID transports, alongside the original standalone Mac path. Implemented code, automated checks,
physical device evidence and release readiness are separate.

## Headless web preview

- [x] CLI server, device enrollment/list/status/action commands and portable stdio MCP.
- [x] Authenticated web grid; Bluetooth-bound device tiles appear before the first upload.
- [x] Separate device credentials, frame stores, queues, stale-frame guards and cancellation.
- [x] Shared native Swift headless radio reusing the HID descriptor; legacy controller preserved.
- [x] Optional Mac app web client without starting the local radio/receiver.
- [x] Automatic local CA, renewable LAN HTTPS, localhost operator UI and public certificate bootstrap.
- [x] Real encrypted BlueZ and WinRT HID services with targeted/guarded notification routing.
- [x] Shared multi-central CoreBluetooth helper and common per-device control/calibration engine.
- [x] Bluetooth-first peer selection, persistent per-peer links and terminal QR onboarding.
- [x] Idempotent POSIX/PowerShell setup, runtime/dependency installation, agent registration and uninstall.
- [x] Automated multi-device transport/control-isolation tests and synthetic native-helper checks.
- [ ] Physical Safari sharing, calibration and HID validation on the new headless path.
- [ ] Reliable simultaneous iPhone HID through one or multiple host radios; measure OS limits.
- [ ] Hardware verification of the implemented BlueZ/WinRT HID paths and host/driver test matrix.
- [ ] Token rotation/revocation, Windows ACL verification, streaming performance and packaging.

See [headless server setup and platform status](docs/headless-server.md).

## Previously demonstrated standalone Mac behavior

- [x] Native Mac onboarding, automatic receiver/radio startup and live preview.
- [x] Local HTTPS capture with a private Wi-Fi address, per-installation token and local CA.
- [x] Safari sharing page for iPhone screen capture; no iPhone app required.
- [x] Bundled MCP server with natural-language agent workflow and optional device argument.
- [x] Two-click calibration and per-device/profile/version/orientation reuse.
- [x] Faster paced pointer path, bounded measured retry and stale-capture guards.
- [x] Native bottom-tab selection on the test iPhone.
- [x] Reels and Shorts next/previous gestures, distinct from list scrolling.
- [x] Scope cleanup: one local architecture, native capture settings and matching build checks.

## Next platforms and devices

- [ ] **ESP32 support.** Planned standalone Bluetooth HID bridge; an iPhone and ESP32
      should be enough to control the phone, with no Mac or computer needed at runtime.
- [ ] **Raspberry Pi support.** Planned compact host for screen receiving, MCP and HID
      control; explore a setup that can run beside the iPhone without a Mac or PC.
- [ ] **Linux and Windows distribution.** Portable HTTP/TLS, CLI, registry and MCP are
      implemented; native HID is implemented but not yet hardware-verified. Validate packages and test on each host.
- [ ] **Multi-iPhone physical control.** The web server supports independent sessions and
      native-helper routes; validate simultaneous radio/pairing behavior on real phones.

## After the experimental 0.0.1 release

- [ ] Verify clean installation on another Mac and iPhone, including certificate transfer,
      permissions, pairing and a fresh MCP client.
- [ ] Expand the compatibility matrix: OS/app versions, pointer settings, orientation,
      reconnects, screen lock and long-running capture.
- [ ] Validate TikTok and additional feed layouts independently.
- [ ] Improve connection recovery and diagnostics without blind duplicate input.
- [ ] Package a signed/notarized distribution with an understandable first-run flow and
      installation instructions per MCP client.
- [ ] Review transport, local token lifecycle, certificate handling and dependency licenses.
- [ ] Record a public demo using synthetic content and the actual packaged app.

Version 0.0.1 is an experimental, non-notarized development build. No cross-device compatibility guarantee,
public release, auto-unlock or automatic system-permission approval is claimed.

- [x] CLI-only distribution; default background server and tokenless loopback operator UI.
- [x] Delayed-frame-safe calibration, recent per-device frame IDs and live connection lights.
