# Open iPhone Use

Let an AI agent use your own iPhone. Connect the phone to your Mac by cable or Wi-Fi, and
Open iPhone Use streams its screen live and taps, swipes, types and presses buttons on it
through Xcode's CoreDevice services. Your agent connects over MCP. The server contains no
model or AI API key.

- **Live screen:** the same HEVC stream Xcode mirrors, about 45 frames per second over USB.
- **Exact input:** touches land on the exact pixel, so there is no pointer to calibrate. Text,
  key chords and the Home, Lock, volume and Siri buttons work too.
- **Nothing on the phone:** no app, no Bluetooth pairing, no certificate, no Safari tab.

![Two iPhones live at once in Instagram Reels, an iPhone SE over USB and an iPhone 13 over Wi-Fi; both move to the next Reel together](docs/media/two-phones.gif)

> **Using Xcode 26 or iOS 26?** Install
> [v0.0.3](https://github.com/semsturgut/open-iphone-use/releases/tag/v0.0.3), the last release
> that works without Xcode 27 (Bluetooth HID and Safari screen sharing):
> `curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --version v0.0.3`

## Requirements

- A Mac with **Xcode 27 or later**. Open Xcode once while the iPhone is connected, so it
  installs its developer tools on the phone.
- An iPhone on **iOS 27 or later**, connected by USB, with **Trust This Computer** accepted.
- **Developer Mode** on the iPhone: **Settings → Privacy & Security → Developer Mode**. The
  phone restarts and asks you to confirm.
- If the phone shows **Settings → Developer → UI Automation**, turn it on.

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash
```

```sh
open-iphone-use serve
```

```sh
open-iphone-use new
```

`serve` starts in the background and prints a `Web UI:` link (`http://127.0.0.1:27893`).
`new` adds the connected phone; with several phones it asks which one, or takes
`--udid UDID` (`open-iphone-use candidates` lists them). You can also select **Add phone** in
the Web UI.

**Watch it live.** The Web UI shows each phone's screen live; click the screen to tap. It is
loopback-only and needs no token, so any local process can operate it: do not expose it
through a proxy.

```sh
open-iphone-use devices                    # list added phones
open-iphone-use status --device DEVICE_ID  # connection details
open-iphone-use remove DEVICE_ID           # remove a phone and stop its control
open-iphone-use stop                       # stop the server
```

To uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --uninstall
```

See [installation](docs/installation.md) for options and development setup.

## Wi-Fi

Once Xcode has paired the phone over a cable, macOS can also reach it wirelessly. When the
cable is unplugged and both are on the same network, the phone shows up as **Wi-Fi** in
`open-iphone-use candidates` and the Web UI, and can be added and controlled without the
cable. If it does not appear, open Xcode's Devices window once with the phone nearby. The phone and Mac must be on the same network. Over Wi-Fi the screen arrives at a lower
frame rate (about 12 fps in a first test) and takes longer to start.

To reconnect, plug the cable back in or bring the phone onto the same Wi-Fi: the server keeps
retrying and the phone's screen comes back in the Web UI by itself. Added phones are remembered
across server restarts.

## MCP

Setup registers the MCP server and agent skill for detected clients (Codex, Claude Code,
Cursor, OpenCode, Command Code). Use an image-capable agent. The tools:

- **Setup and status:** `list_candidates`, `create_device` (with `udid`), `remove_device`,
  `list_devices`, `get_device_status`, `get_latest_screenshot`, `stop_control`.
- **Input:** `ensure_session`, `pointer_tap`, `type_text`, `send_key`, `swipe`, `scroll`,
  `advance_feed`. `send_key` also takes `home`, `lock`, `volume_up`, `volume_down`, `mute` and
  `siri`.

How the tools work:

- **Coordinates:** fractions of the full returned image (`x = px / width`, `y = py / height`).
- **Frames:** every action needs a frame ID captured after the phone's last input. Each action
  returns a new frame, so the agent can check the result before the next step.
- **Verifying results:** input is serialized per phone. An `ok: true` result does not prove the
  UI changed, so inspect the returned screen after every action.
- **iOS rules still apply:** the owner unlocks the phone, and iOS permissions and protected
  content stay in force.

## Limits

- One USB phone per server; more phones can join over Wi-Fi.
- Tested on an iPhone 13 (iOS 27.0, USB and Wi-Fi) and an iPhone SE (iOS 27.0.1, USB), also
  both at once.
  Multi-touch and long sessions are not tested yet.
- The screen stream will not start while an app is using the camera or microphone.
- macOS only: the stream is decoded with VideoToolbox.

See [how it works](docs/coredevice.md) for details and the validation record. Read the
[changelog](CHANGELOG.md), [roadmap](ROADMAP.md) and [contributing guide](CONTRIBUTING.md).
