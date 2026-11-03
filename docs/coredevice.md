# USB control (CoreDevice)

A USB phone can be controlled without Bluetooth HID, AssistiveTouch or Safari sharing. The
server opens Xcode's CoreDevice services over a rootless, in-process RSD tunnel
([pymobiledevice3](https://github.com/doronz88/pymobiledevice3)'s userspace tunnel):

- **Screen:** the HEVC stream Xcode mirrors, decoded with VideoToolbox through
  pymobiledevice3's VNC pipeline (keyframe recovery and the RTCP keepalive included). A static
  screen sends no new video, so the last frame is resent every 0.5 s to keep captures fresh.
- **Input:** dtuhidd's universal HID service. Touches are absolute (`0…65535` across the
  display), so there is no pointer, calibration or landing correction. Text and keys use a
  host-registered virtual keyboard; hardware buttons use Indigo HID. The video stream must stay
  up: it holds the HID authentication open.
- **Fallback:** with an older Developer Disk Image the phone publishes neither service. The
  backend then uses screenshots (CoreDevice screen capture or DVT) and reports why input is
  unavailable.

## Requirements

- macOS host. The installer includes pymobiledevice3.
- iPhone on iOS 27 or later, connected by USB, trusted by this Mac, with **Developer Mode** on.
- For the stream and input: Xcode 27 or later, opened once with the phone connected so it
  mounts its Developer Disk Image. If the phone shows **Settings › Developer › UI Automation**,
  enable it.
- One CoreDevice phone per server process: the userspace network stack is process-global.
- The stream does not start while a foreground app uses the camera or microphone.

## Use

```sh
open-iphone-use coredevices                # USB phones with name, iOS version and UDID
open-iphone-use new                        # add the connected phone (or --udid UDID)
```

The Web UI streams each phone live as MJPEG at up to 30 fps
(`/api/devices/DEVICE_ID/stream`, loopback only); click the screen to tap.

MCP clients call `list_coredevice_candidates`, then `create_device` with `udid`. Every other
tool is unchanged:

- `ensure_session` checks that input is available instead of calibrating; `return_app`
  opens an app through Spotlight.
- `pointer_tap` touches the exact fraction given. `pointer_move` only sets the target that
  `click` touches. `move_pointer` (relative motion) is refused.
- `send_key` takes the usual keys and chords. `home`, `lock`, `volume_up`, `volume_down`, `mute`
  and `siri` press hardware buttons; Cmd+H does not go home on an iPhone.
- `stop_control` cancels the running action and releases the finger and keys.

## Validation record

iPhone 13 (iPhone14,5), iOS 27.0, USB, macOS host.

With the Xcode 26.2 Developer Disk Image:

- The rootless tunnel connected in about 1.5 s; DVT screenshots arrived at about 1.4 frames
  per second at full resolution (1170×2532).
- The phone published neither the display nor the universal HID service, and the backend
  reported the requirement instead of sending input.

With Xcode 27.2 installed and opened:

- The phone published `com.apple.coredevice.displayservice`, `…hid.universalhidservice`,
  `…hid.indigo` and `…screencaptureservice`.
- The first decoded frame arrived 1.2–1.4 s after start; the stream delivered 45–46 frames per
  second at 1184×2576 (the encoder's padded size).
- Checked on the returned frames:
  - Swipes moved between Home Screen pages.
  - `ensure_session` with `return_app="Settings"` opened Settings, and a swipe scrolled it.
  - Cmd+Space opened Spotlight, and `type_text` entered `hello iPhone 123` with correct case.
  - `home` returned from Spotlight to the Home Screen.
  - `pointer_tap(0.5, 0.818)` hit the Home Screen Search button and opened Spotlight.
- Closing the backend took about 0.1 s.

iPhone SE 2nd generation (iPhone12,8), iOS 27.0.1, through the Web UI:

- The phone was listed with its name and iOS version, and **Add phone** brought up its live
  MJPEG screen.
- Checked on the live screen:
  - Clicking the Calendar icon opened Calendar.
  - **Home** returned to the Home Screen.
  - **Swipe down** opened search.
  - **Type** entered `Hello SE 27`.

Not checked: other devices or iOS versions, long sessions, Wi-Fi, pinch and multi-touch,
apps that use the camera, and the screenshot fallback's CoreDevice capture path.
