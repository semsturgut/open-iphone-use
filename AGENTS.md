# Open iPhone Use contributor instructions

- Scope: a native macOS SwiftUI app, its bundled stdio MCP server, and iPhone screen sharing / BLE HID. See README.md and ROADMAP.md.
- Keep macOS and iOS native Swift. The local HTTPS receiver belongs to the Mac app; agents supply their own model through an MCP client.
- Never commit credentials, local signing settings, capture tokens, private keys or personal screenshots.
- Preserve owner-controlled Bluetooth pairing, certificate trust, screen sharing and unlocking.
- Record tests, builds, installation and physical iPhone behavior separately. A delivered HID report does not prove UI success.
- Keep input serialized, stop on stale capture, release held input on cancellation and verify the screen after each action.
- Run `swift test --package-path macos`, the capture-page and MCP discovery checks, and the iOS capture checks for affected code. See CONTRIBUTING.md.
- Follow ROADMAP.md for remaining distribution work; do not claim universal device support.
- Authenticated App Store Connect reads/delivery belong on Ubuntu via asc. iOS release build/signing/upload belongs on Codemagic, only with explicit delivery authorization. Local development builds are separate.
