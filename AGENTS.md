# Open iPhone Use contributor instructions

- Scope: a macOS CLI server that controls iPhones through Xcode's CoreDevice services (USB or Wi-Fi), its loopback Web UI and its stdio MCP server. See README.md and ROADMAP.md.
- Agents supply their own model through an MCP client; the server holds no model key.
- Never commit credentials, local signing settings, capture tokens, private keys or personal screenshots.
- Preserve owner-controlled trust, Developer Mode, pairing and unlocking; never automate them.
- Record tests, installation and physical iPhone behavior separately. A delivered input does not prove UI success.
- Keep input serialized, stop on stale capture, release held input on cancellation and verify the screen after each action.
- Run the server tests and checks in CONTRIBUTING.md for affected code.
- Follow ROADMAP.md for remaining distribution work; do not claim universal device support.
- Authenticated App Store Connect reads/delivery belong on Ubuntu via asc. iOS release build/signing/upload belongs on Codemagic, only with explicit delivery authorization. Local development builds are separate.
