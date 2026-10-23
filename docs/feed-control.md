# Full-screen video control through MCP

Open iPhone Use exposes different actions for lists and video feeds. This behavior lives in
the bundled MCP server and native bridge, not a chat-specific prompt or a model provider.

## Client workflow

1. Call `ensure_session`. It checks Bluetooth/capture and reuses calibration for the
   paired device, motion version and screen aspect; a new phone needs its own calibration.
2. Open the app and its full-screen Reels/Shorts/TikTok viewer. Inspect the current item.
3. Call `advance_feed(dy: 1)` for next, or `advance_feed(dy: -1)` for previous.
4. Inspect the returned frame. If the transition is unfinished, read
   `get_latest_screenshot` before deciding whether it worked. Confirm account/title or
   item identity; changing video pixels alone does not prove navigation.
5. Repeat only after confirming the previous transition. A dialog, comments pane,
   advertisement or loading screen may require a different action.

Do not repeat an uncertain like, comment or submission. Feed navigation never presses
like, follow, subscribe, playback or share controls deliberately.

## What the native bridge sends

`advance_feed` defaults to `mode: swipe`: it moves to a calibrated central location,
presses the left mouse button, drags vertically, and releases. Next goes from whole-screen
(0.5,0.76) toward (0.5,0.24); previous reverses it. HID reports use the measured motion
profile and BLE backpressure. Press/drag/release is one serialized action. Error and stop
paths release input. It does not start a drag at the status bar or from a side action rail.

`scroll(dy: ...)` sends HID wheel reports for lists/grids. It is not the default video
navigation action. An explicit `advance_feed(mode: wheel, steps: 120)` fallback exists
for app/device combinations where a wheel works; it is not an automatic blind retry.
Wheel sensitivity differs by app/device. Both feed directions accept one item per call:
`dy` must be 1 or -1. A successful HID response confirms input delivery, not an exact
semantic item count. `swipe(direction: ...)` is the general directional drag tool.

Implementation: `OpenIPhoneUseController+Pointer.swift` (`advanceFeed`, `swipeScreen`),
`BLEHIDPeripheral.swift` (`swipe`), `OpenIPhoneUseMCPServer.swift` (tool schemas/parsing),
and `AgentWorkflow.swift` (MCP initialization instructions).

## Installing on another Mac

Run the Mac app, pair that Mac with the iPhone, enable AssistiveTouch, approve screen
sharing, and register that installation's bundled `open-iphone-use-mcp` executable in the
chosen MCP client. See `macos/README.md` for paths/configuration and local HTTPS setup.
Do not copy another owner's capture token, certificate private key or saved calibration.
The capture URL belongs to that Mac; the device is calibrated locally. No provider API
key is required by the bridge. The model comes from the chosen MCP client.

MCP initialization supplies the workflow, so the owner can ask for ordinary actions
without naming tools. Registration is per client; reconnect after upgrading the helper
so the client reloads its catalog. The bridge has no public MCP TCP listener. Pairing,
AssistiveTouch and iOS sharing consent remain owner setup steps.

## Physical evidence, 2026-10-23

Paired portrait iPhone, 750×1334, `fast16` two-click calibration, full-screen native apps:

| App | Forward gestures | Backward gestures | Observed result |
| --- | ---: | ---: | --- |
| Instagram Reels | 2 | 1 | Two different accounts appeared in sequence; backward returned to the preceding account and same reel. |
| YouTube Shorts | 2 | 1 | Two different channel/title pairs appeared; backward returned to the preceding channel/title. |
| TikTok | Not tested in this run | Not tested in this run | Uses the same tool; physical compatibility remains unverified here. |

All six tested gestures used default `mode: swipe`, with no wheel fallback and no manual
pause/resume taps. Each transition was visually inspected separately. A second account
or title proved navigation; transport success alone was not counted. No like, follow,
comment or subscription was requested or deliberately sent.

These results establish the installed path on this device. Other iOS versions, pointer
settings, app versions and Macs still require bring-up testing; they are not universal
compatibility evidence. The protocol is model-independent, but an agent must still
recognize the correct screen and honor the verification workflow.
