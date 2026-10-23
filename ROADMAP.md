# Open iPhone Use roadmap

The current scope is Mac + iPhone: native Mac app, bundled stdio MCP server, local screen
sharing and Bluetooth HID. Implemented behavior and release readiness are separate.

## Demonstrated

- [x] Native Mac onboarding, automatic receiver/radio startup and live preview.
- [x] Local HTTPS capture with a private Wi-Fi address, per-installation token and local CA.
- [x] Safari sharing page and optional native ReplayKit capture source.
- [x] Bundled MCP server with natural-language agent workflow and optional device argument.
- [x] Two-click calibration and per-device/profile/version/orientation reuse.
- [x] Faster paced pointer path, bounded measured retry and stale-capture guards.
- [x] Native bottom-tab selection on the test iPhone.
- [x] Reels and Shorts next/previous gestures, distinct from list scrolling.
- [x] Scope cleanup: one local architecture, native capture settings and matching build checks.

## Next platforms and devices

- [ ] **Linux and Windows builds.** Native transport/input support must be implemented
      and tested per platform; the macOS bridge is not portable by changing a build flag.
- [ ] **Multi-iPhone support.** Per-device pairing, capture credentials, calibration,
      routing and action queues; the current version controls one phone.

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
