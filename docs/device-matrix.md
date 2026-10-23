# Device checks

Results are scoped to the exact path tested. Build success, installed software,
Bluetooth report acceptance and actual UI behavior are distinct evidence.

## Current Mac + iPhone demo — 2026-10-23

| Component | Observed result |
| --- | --- |
| Mac | Apple Silicon development build; native app and bundled stdio MCP helper |
| iPhone | Paired portrait device, 750×1334 captured image; no device identifier published |
| Capture | Safari sharing over private Wi-Fi HTTPS; frames continued while using native apps |
| Input | Mac Bluetooth HID with AssistiveTouch |
| Calibration | `fast16`, two measured clicks, span 389×693 units |
| MCP | Fresh-process initialization/discovery and live tool calls verified |

The physical run did not independently establish the phone's exact model/OS or test a
second Mac. Do not infer those facts from an earlier device inventory or a screen clock.

### Calibration and pointer

- Preparation: **3532 ms**, exactly two calibration clicks and no profile search.
- Compatible saved-model reuse: **79 ms**, no input or recalibration.
- `pointer_move(0.75,0.80)`: **1062 ms**, including fresh helper startup/response.
- Independent board landing: (0.7572,0.7958) for target (0.75,0.80).
- Independent board landing: (0.2622,0.2424) for target (0.25,0.25).
- Worst axis error across those two targets: **1.22%**. They did not train the model.
- Native Clock bottom Stopwatch tab opened on its first tap at (0.62,0.915).
- No notification/date sheet appeared during this run. No stopwatch or timer was started.

The saved model remains truthfully labeled `twoPointEstimate`, with no invented held-out
score. These limited checks do not establish every app hit target or pointer setting.
An earlier slow profile took 7074 ms toward (0.75,0.75); targets differ from the current
measurement, so this is not a controlled speed ratio.

### Full-screen video

| App | Next | Previous | Visual confirmation |
| --- | ---: | ---: | --- |
| Instagram Reels | 2/2 | 1/1 | Different account/reel appeared; previous restored its predecessor |
| YouTube Shorts | 2/2 | 1/1 | Different channel/title appeared; previous restored its predecessor |
| TikTok | Untested | Untested | No physical compatibility claim |

All six gestures used the installed MCP `advance_feed` default swipe, no wheel fallback
or pause/resume clicks. Each item was inspected separately. No like, comment, follow or
subscription action was requested or deliberately sent during those six gesture checks. [Detailed contract](feed-control.md).

### Release recording repeat

The 0.0.1 app completed another two-click preparation in **2629 ms** (`fast16`,
389×765 estimated units). This run did not measure independent pointer error.
Through the bundled MCP helper, the agent launched Instagram with keyboard search,
searched **quick dinner recipes**, opened a cooking reel, and advanced twice. The
account changed from `cookinginthemidwest` to `aflavorfulbite` to `eatwitzo`.
The final Chicken Bacon Ranch Pasta reel was initially unliked; one tap turned its
heart red, verified in the live Mac view. The owner recorded the Mac screen.
No comment, follow, repost or direct message was sent.

## Still to verify

Clean setup on another Mac/iPhone, OS/app version variations, pointer settings,
orientation, reconnects, lock/protected screens, network isolation and long-running
capture. Calibration is per device; do not copy it to another installation.

Never attach device serials, Bluetooth addresses, tokens or personal screen content.
