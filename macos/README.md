# Open iPhone Use for Mac

Native SwiftUI app + bundled stdio MCP helper. The Mac receives iPhone screen images
on private Wi-Fi and sends Bluetooth keyboard/pointer input back. Your MCP client provides
the AI model. See [architecture](../docs/architecture.md) and [physical evidence](../docs/device-matrix.md).

## Build

Use Xcode's Swift toolchain. The package declares macOS 13 minimum; physical verification
currently covers an Apple Silicon development machine, not every supported Mac/OS.

```sh
cd macos
swift test
./build-app.sh
open "$HOME/Applications/Open iPhone Use.app"
```

The script bundles both executables and capture resources, then signs the development
app. Its default is ad hoc signing. For repeated physical Bluetooth development builds,
set `OPEN_IPHONE_USE_CODESIGN_IDENTITY` to your own Apple Development signing identity so
privacy permissions can remain associated with the app. No signing credential is shipped.
Build to a separate destination when the installed app is running:

```sh
./build-app.sh /absolute/staging/Open iPhone Use.app
```

This is a development bundle, not a notarized public distribution.

## Pair and share

1. Open Open iPhone Use. The app starts its radio and receiver automatically; allow macOS's
   Bluetooth permission if requested.
2. On iPhone enable **Settings → Accessibility → Touch → AssistiveTouch**, then pair with
   **Open iPhone Use** under its Devices → Bluetooth Devices. Confirm system prompts yourself.
3. Keep both devices on the same private Wi-Fi. Use **Share root certificate** to transfer
   only the public CA certificate through a trusted channel, such as AirDrop.
4. On iPhone install it and explicitly enable trust under **Settings → General → About →
   Certificate Trust Settings**. Open iPhone Use cannot approve certificate trust for you.
5. Copy/share the capture link shown by the Mac. Open it in iPhone Safari, tap **Start
   sharing**, and approve iOS screen sharing. The Mac switches to live preview.
6. Keep Safari visible for the first calibration. The agent's `ensure_session` performs
   two measured clicks when needed. Compatible mapping is reused without further clicks.

Sharing startup is read-only and does not send surprise pointer input. If the gray
pointer cannot be seen safely within the calibration board, preparation stops before
pressing. Changing pointer appearance/settings can require a new physical check.

The token persists across app restarts. The link remains valid while the Wi-Fi address
and token are unchanged. A stopped broadcast still requires the owner's system approval
again. Capture can pause or stop when switching apps; the tested phone continued sharing
through native Reels/Shorts. Do not assume the same result on every iOS version. The
iPhone uses its built-in Safari screen-sharing page; no iPhone app is installed.

## Register the MCP helper

Register this installation's `Open iPhone Use.app/Contents/Helpers/open-iphone-use-mcp` executable
as a stdio server in your MCP client. For a standard installation, a TOML client entry is:

```toml
[mcp_servers.open-iphone-use]
command = "/Users/your-short-name/Applications/Open iPhone Use.app/Contents/Helpers/open-iphone-use-mcp"
startup_timeout_sec = 15
tool_timeout_sec = 240
enabled = true
```

No repository-specific working directory is required. Reconnect the client's MCP server
when adding/upgrading the helper so its tool catalog and workflow refresh. Registration
is per client, not per chat. Open iPhone Use does not register itself in every installed agent.
The helper stores no AI key and does not run a model.

A new chat can receive a normal request such as “Open iPhone Use, open Instagram.” MCP
initialization supplies the workflow. The client/model must support images and follow
its instructions and confirmation rules. Owner-only setup prompts remain owner actions.

## Main tools

| Tool | Use |
| --- | --- |
| `guide` | Workflow and practical limits |
| `ensure_session` | Check capture/Bluetooth, reuse mapping or prepare it |
| `prepare_session` | Force preparation after pointer-setting changes |
| `get_device_status` | Connection, capture age, calibration evidence and transport counters |
| `get_latest_screenshot` | Current JPEG, frame ID, receipt time and age |
| `pointer_move`, `pointer_tap` | Whole-screen fractions 0…1; no Safari offsets |
| `type_text`, `send_key` | Supported US-layout ASCII and keyboard chords |
| `scroll` | HID wheel for lists/grids |
| `advance_feed` | One centered swipe for next/previous reel or short |
| `swipe` | General directional press/drag/release |
| `pointer_retry_tap` | One bounded correction using an actually observed landing |
| `stop_control` | Cancel input and attempt key/button release |

Device arguments default to `iphone`. Raw movement, board display, pointer lookup and
full probes remain explicit diagnostics, not the ordinary agent route. `pointer_jump`
compatibility aliases use the same calibrated path; they do not bypass it.

For app launch, use system Search: `send_key cmd+h`, `send_key cmd+space`, replace its
query, type an app name, inspect the result and press Enter. Focus a field before typing.
Text is limited to 500 supported ASCII characters; unsupported text fails before sending.

## Calibration and video gestures

Startup uses two known moves and two DOM-measured clicks at the paced `fast16` profile,
without profile search or extra hover tours. A fresh frame must show the pointer in the
visible white board before each click. The two-point estimate is bound to paired device,
screen aspect, motion profile and version. It is not independent full-screen proof.
Full `pointer_probe` is an explicit diagnostic with separate held-out targets.

Normal absolute movement re-homes with buttons released, then moves vertically away
from the top edge before horizontal travel. Report pacing and BLE backpressure preserve
ordering. The final drain fence means transport acceptance, not proof of UI success.
Clicks hold for 30 ms before release; action frames include a 250-ms display margin,
but app transitions may still need another screenshot. Right-button behavior depends on
iPhone AssistiveTouch settings.

`advance_feed(dy: 1)` / `dy: -1` performs one press/drag/release from safe central screen
fractions. Inspect the account/title after each transition; video pixel animation is not
proof. Wheel fallback is explicit, without blind playback clicks. [Feed contract and test scope](../docs/feed-control.md).

On the paired test phone, preparation took 3.532 s, reuse 79 ms and a measured move
1.062 s. Two independent board targets had worst-axis error 1.22%; a native bottom tab
opened on its first tap. Reels/Shorts passed six inspected transitions. Other setups still
need device checks; no universal accuracy/compatibility guarantee is implied.

## Local data and safety

The receiver uses HTTPS on private Wi-Fi IPv4 port 27890, checks the connecting subnet,
requires the per-installation token and accepts bounded JPEGs. Only the newest frame
stays in Mac memory. CA/TLS private keys stay under the private application-support
directory; share only the public root certificate. Safari validates TLS and blocks
redirects. No cleartext mode is provided.

MCP uses stdio and a same-user Unix socket at
`~/Library/Application Support/Open iPhone Use/bridge.sock` (directory 0700, socket 0600).
It has no network MCP listener. Capture older than five seconds blocks control. Stop
cancels queued actions and attempts release; disconnect can prevent release delivery.
A known frame up to five seconds old can guard an action, but does not prove its target
is still unchanged. Inspect each action's screen before claiming success.

Screenshots read by an MCP client may go to its model provider. The bridge does not
control the client's inference privacy or approval rules. Read [security](../SECURITY.md).

## Checks

```sh
swift test
swift build -c release
node scripts/test-capture-page.mjs
python3 scripts/test-agent-mcp.py .build/release/open-iphone-use-mcp
```

The stdio compatibility adapter ignores unsupported nested experimental capabilities
only during initialization; standard requests/IDs remain unchanged. Discovery tests
cover a fresh client's workflow and schemas without sending iPhone input. Unit/build
checks and real-device evidence must be reported separately.
