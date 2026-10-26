# Changelog

## Unreleased — CLI-first multi-phone workflow

- Align portable and native HID scroll signs; `advance_feed(dy=1)` targets the next item.
- Detect the calibration board separately from charging/status icons, retry stable frames,
  close interrupted boards, and fit seven fixed-cadence samples per phone.
- Verify visually settled pointer positions and correct bounded misses before coordinate
  clicks; reject ambiguous or non-converging targets without clicking.


- Install one canonical agent skill with Claude/Command Code aliases; register OpenCode
  (preserving JSONC comments) and Command Code alongside Codex/Claude/Cursor, migrate old
  application commands and clean stale skill copies/backups.
- Keep stdio MCP available while the server is down, return actionable tool errors, handle
  malformed requests/closed pipes and write rotating diagnostics outside stdout.
- Restore macOS peripheral identity/services/per-central subscriptions when available and
  resume connectable advertising across macOS/BlueZ/WinRT interruptions; keep OS bonds.
  Actual automatic iPhone reconnect remains subject to OS behavior and hardware verification.

- Wait for delayed, stable pointer/board frames and matching tap feedback before calibration
  clicks; extend corner homing and keep preparation independent per phone.
- Accept recent per-device frame IDs for ordinary input; preparation takes its own frames.
- Continue GAP name resolution past generic cached names and distinguish duplicate labels.
- Start `serve` in the background by default; add `--foreground` debugging and clean startup
  output. Loopback UI/API access is automatic, with no operator token; LAN capture remains
  protected by each device's capture token.
- Render width-aware, black-on-white terminal QR codes using Unicode half-blocks.
- Remove DMG/application packaging, desktop installers, release references and artifacts;
  installation/builds use setup.sh/setup.ps1 and the CLI.
- Show live Bluetooth and Screen share lights and matching CLI/MCP status messages.

## 0.0.2 — experimental alpha

- Video-only Safari capture avoids requesting microphone audio or interrupting autoplay.
- Retain the inaudible WebAudio session used for Safari background continuation.

## 0.0.1 — experimental alpha

- Initial Bluetooth keyboard/pointer, local HTTPS Safari capture, MCP workflow and two-click
  calibration. Historical device evidence is separate from current multi-phone validation.
