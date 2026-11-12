# Changelog

## 0.2.0 — Accessibility refs and compact agent calls

- Add a Python DTX accessibility layer on the existing CoreDevice RSD connection, using
  `com.apple.accessibility.axAuditDaemon.remoteAXService`. Admit matching initial and
  additional tree dumps and return compact `ref|action|role|label|value` rows.
- Add `get_ui_tree` and `tap_ref`. Ref taps check live element token, label, value, geometry,
  enabled state and visibility, with frontmost hit testing before HID delivery. Rejected
  checks send no touch or cleanup input. Refs expire after 120 seconds or reconnect.
- Add serialized batches of up to 16 steps, upfront syntax validation, per-step receipts,
  cancellation and stop-on-first-failure behavior.
- Add `image_mode` with full JPEG, 640-pixel preview and metadata-only options. Include a
  native JPEG SHA-256 screen hash. Existing tools keep full images by default, and the new
  tree, ref and batch tools default to metadata-only responses.
- Reduce USB post-input frame latency from 150 ms to 30 ms. Wi-Fi keeps 150 ms. Remove the
  additional 200 ms sleep after swipes and scrolls, retaining post-input frame checks.
- Update the bundled agent skill for accessibility-first use and ship AXe's MIT attribution.
- Pin pymobiledevice3 to the physically verified 11.49.5. Version 11.52.1 changes the
  private frame callback signature used by the current stream integration.
- Preserve existing pairing, USB/Wi-Fi tunnel selection and connection startup paths.

## 0.1.1 — Cooler Wi-Fi streaming

- Cap the Wi-Fi screen stream at 2 Mbit/s (USB stays at 6 Mbit/s): about half the radio traffic,
  to reduce heating during long sessions. `get_device_status` reports the negotiated bitrate.

## 0.1.0 — USB and Wi-Fi control through CoreDevice (unreleased)

- Control phones through Xcode's CoreDevice services: the HEVC screen stream Xcode mirrors
  (about 45 fps over USB), exact touches, a virtual keyboard and hardware buttons. Needs
  Xcode 27, iOS 27 and Developer Mode. USB uses a rootless in-process tunnel; Wi-Fi rides the
  tunnel macOS keeps for a wirelessly paired phone (about 12 fps in a first test).
- `open-iphone-use new` adds a connected phone; `candidates` lists them with name, iOS and
  connection. The Web UI streams each phone live; click the screen to tap.
- MCP: `list_candidates` and `create_device(udid)`; `send_key` presses hardware buttons.
  Removed the pointer and calibration tools (`move_pointer`, `pointer_home`, `set_page_board`,
  `pointer_quick_probe`, `prepare_session`) and `list_bluetooth_candidates`.
- Removed Bluetooth HID, Safari screen sharing, pointer calibration, the LAN HTTPS receiver
  and its certificates, the Swift helper, and Linux/Windows support. For Xcode 26 / iOS 26,
  install v0.0.3. Phones added under earlier releases are dropped from the saved state.
- Verified on an iPhone 13 (iOS 27.0, USB and Wi-Fi) and an iPhone SE (iOS 27.0.1, USB).

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
