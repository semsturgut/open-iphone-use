# Open iPhone Use

Let an AI agent use your own iPhone. Connect the phone to your Mac with a cable, and
Open iPhone Use streams its screen live and taps, swipes, types and presses buttons on it
through Xcode's CoreDevice services. Your agent connects over MCP. The server contains no
model or AI API key.

- **Live screen:** the same HEVC stream Xcode mirrors, about 45 frames per second.
- **Exact input:** touches land on the exact pixel, so there is no pointer to calibrate. Text,
  key chords and the Home, Lock, volume and Siri buttons work too.
- **Nothing on the phone:** no app, no Bluetooth pairing, no certificate, no Safari tab.

> **Using Xcode 26 or iOS 26?** The previous release works without Xcode 27, using Bluetooth
> HID and Safari screen sharing. Install it with
> `curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --version v0.0.3`
> and follow the [legacy Bluetooth setup](docs/bluetooth-legacy.md).

## Requirements

- A Mac with **Xcode 27 or later**. Open Xcode once while the iPhone is connected, so it
  installs its developer tools on the phone.
- An iPhone on **iOS 27 or later**, connected by USB, with **Trust This Computer** accepted.
- **Developer Mode** on the iPhone: **Settings → Privacy & Security → Developer Mode**. The
  phone restarts and asks you to confirm.
- If the phone shows **Settings → Developer → UI Automation**, turn it on.

## Quick start

**Install**

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash
```

**Start**

```sh
open-iphone-use serve
```

It starts in the background and prints a `Web UI:` link (`http://127.0.0.1:27893`).

**Add your iPhone**

```sh
open-iphone-use new
```

Or select **Add phone** in the Web UI. With several phones connected, `open-iphone-use
coredevices` lists them and `open-iphone-use new --udid UDID` picks one.

**Watch it live.** The Web UI shows each phone's screen live. Click the screen to tap, and use
the Home, swipe and typing controls under it. The Web UI is loopback-only and needs no token,
so any local process can operate it: do not expose it through a proxy.

**Stop or uninstall**

```sh
open-iphone-use stop
```

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --uninstall
```

`serve --foreground` runs in the terminal for debugging. The server is not a login or reboot
service. See [Advanced installation](docs/installation.md#advanced-installation) for options
and development setup.

```sh
open-iphone-use devices                    # list registered phones
open-iphone-use status --device DEVICE_ID  # inspect a phone
open-iphone-use remove DEVICE_ID           # remove a phone and stop its control
```

## MCP

Setup registers the MCP server and agent skill for detected clients (Codex, Claude Code,
Cursor, OpenCode, Command Code). Use an image-capable agent. The tools:

- **Setup and status:** `list_coredevice_candidates`, `create_device` (with `udid`),
  `remove_device`, `list_devices`, `get_device_status`, `get_latest_screenshot`,
  `stop_control`.
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

- One USB phone per server process.
- Tested on one iPhone 13 on iOS 27.0. Wi-Fi, multi-touch and long sessions are not tested yet.
- The screen stream will not start while an app is using the camera or microphone.
- macOS only: the stream is decoded with VideoToolbox.

See [USB control (CoreDevice)](docs/coredevice.md) for how it works and the validation record.
Read the [changelog](CHANGELOG.md), [documentation](docs/) and
[contributing guide](CONTRIBUTING.md).
