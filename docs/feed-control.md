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

## What the portable server sends

`advance_feed(dy: 1)` performs an upward drag to request the next item; `dy: -1`
requests the previous item. `scroll(dy: ...)` sends raw HID wheel ticks for lists and grids.
App sensitivity varies. Input is serialized and released on cancellation. A successful HID
response does not establish the displayed item: inspect the screen after each gesture.
The current implementation is in `server/oiu/control.py` and `server/oiu/mcp.py`.

## Installing on another host

Install with `setup.sh` on macOS/Linux or `setup.ps1` on Windows, start the CLI server,
pair the phone, enable AssistiveTouch and approve Safari screen sharing. Setup registers
the portable MCP server for detected clients; see [installation](installation.md). Do not
copy another owner's capture token, private key or calibration. Windows and Linux have not
been tested on real hardware. The model comes from the chosen MCP client.

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

These historical results establish the earlier app path on this device, not the current CLI path. Other iOS versions, pointer
settings, app versions and Macs still require bring-up testing; they are not universal
compatibility evidence. The protocol is model-independent, but an agent must still
recognize the correct screen and honor the verification workflow.
