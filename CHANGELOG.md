# Changelog

## 0.0.3 — CLI-first multi-iPhone release (unreleased)

- Add a curl installer with verified prebuilt macOS helper, source fallback, offline uninstall,
  and release asset workflow.
- Target 30 fps Safari capture; actual frame delivery depends on the phone and network.
- Fit seven calibration samples on the visual pointer, accept a good fit without a held-out
  step, and remeasure a poor fit. Support the default auto-hiding AssistiveTouch pointer.
- Detect landings using visual observations and frame differences; report verified, predicted
  or blind taps, use bounded corrections, refuse unsafe misses, and learn landing offsets.
- Allow explicit recalibration through preparation tools or Safari; failed attempts do not
  permanently lock recalibration, and automatic attempts can retry after three minutes.
- Include image dimensions in screenshot/action results; improve Bluetooth lifecycle and
  subscription handling. Inspect the resulting screen: predicted and blind taps are
  unverified, and even a verified pointer landing does not prove the UI outcome.
- Windows and Linux backends are implemented but have not been tested on real hardware.

- Align portable and native HID scroll signs; `advance_feed(dy=1)` targets the next item.
- Detect the calibration board separately from charging/status icons, retry stable frames,
  close interrupted boards, and fit seven fixed-cadence samples per phone.
- Observe pointer positions and correct bounded misses before coordinate clicks; report
  unverified landings and refuse those outside the safety limits.


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
  installation/builds use install.sh, setup.sh/setup.ps1 and the CLI.
- Show live Bluetooth and Screen share lights and matching CLI/MCP status messages.

## 0.0.2 — experimental alpha

- Video-only Safari capture avoids requesting microphone audio or interrupting autoplay.
- Retain the inaudible WebAudio session used for Safari background continuation.

## 0.0.1 — experimental alpha

- Initial Bluetooth keyboard/pointer, local HTTPS Safari capture, MCP workflow and two-click
  calibration. Historical device evidence is separate from current multi-phone validation.
