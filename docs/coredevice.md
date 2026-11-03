# How it works (CoreDevice)

The server opens Xcode's CoreDevice services on the phone through an RSD tunnel
([pymobiledevice3](https://github.com/doronz88/pymobiledevice3)):

- **Connection:** over USB, pymobiledevice3's in-process userspace tunnel (no root). Over Wi-Fi,
  the tunnel macOS's `remoted` already keeps to a wirelessly paired phone. `remoted` can take that
  connection back briefly; the backend then reconnects by itself, as it does after unplugging.

- **Screen:** the HEVC stream Xcode mirrors, decoded with VideoToolbox through
  pymobiledevice3's VNC pipeline (keyframe recovery and the RTCP keepalive included). A static
  screen sends no new video, so the last frame is resent every 0.5 s to keep captures fresh.
- **Input:** dtuhidd's universal HID service. Touches are absolute (`0…65535` across the
  display), so there is no pointer, calibration or landing correction. Text and keys use a
  host-registered virtual keyboard; hardware buttons use Indigo HID. The video stream must stay
  up: it holds the HID authentication open.
- **Wi-Fi bitrate:** over Wi-Fi the stream is capped at 2 Mbit/s instead of the 6 Mbit/s Xcode
  negotiates, to halve radio traffic and the heat that comes with it. The picture is softer under
  motion. The phone honours the cap but ignores requests for a smaller resolution.
- **Fallback:** with an older Developer Disk Image the phone publishes neither service. The
  backend then uses screenshots (CoreDevice screen capture or DVT) and reports why input is
  unavailable.

## Requirements

- macOS host. The installer includes pymobiledevice3.
- iPhone on iOS 27 or later, trusted by this Mac, with **Developer Mode** on. Connected by USB,
  or over Wi-Fi once Xcode has paired it.
- For the stream and input: Xcode 27 or later, opened once with the phone connected so it
  mounts its Developer Disk Image. If the phone shows **Settings › Developer › UI Automation**,
  enable it.
- One USB phone per server process: the userspace network stack is process-global. Phones on
  Wi-Fi use remoted's tunnels and can be added alongside it.
- The stream does not start while a foreground app uses the camera or microphone.

## Use

```sh
open-iphone-use candidates                 # connected phones: name, iOS, USB or Wi-Fi, UDID
open-iphone-use new                        # add the connected phone (or --udid UDID)
```

The Web UI streams each phone live as MJPEG at up to 30 fps
(`/api/devices/DEVICE_ID/stream`, loopback only); click the screen to tap.

MCP clients call `list_candidates`, then `create_device` with `udid`. Then:

- `ensure_session` checks that input is available instead of calibrating; `return_app`
  opens an app through Spotlight.
- `pointer_tap` touches the exact fraction given. `pointer_move` only sets the target that
  `click` touches.
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

iPhone 13, iOS 27.0, over Wi-Fi (unplugged, same network as the Mac, phone locked):

- The tunnel through `remoted` connected in 0.7 s. The first decoded frame took 13.6 s and the
  stream ran at about 12 fps.
- Checked on the returned frames: `home` woke the Lock Screen, a right swipe opened the Today
  view, and `home` returned to the Lock Screen.

iPhone SE 2nd generation (iPhone12,8), iOS 27.0.1, through the Web UI:

- The phone was listed with its name and iOS version, and **Add phone** brought up its live
  MJPEG screen.
- Checked on the live screen:
  - Clicking the Calendar icon opened Calendar.
  - **Home** returned to the Home Screen.
  - **Swipe down** opened search.
  - **Type** entered `Hello SE 27`.

Both phones at once, iPhone SE over USB and iPhone 13 over Wi-Fi: both streamed live in the Web
UI at the same time. After a server restart, the saved phone reconnected and streamed again
without any action.

iPhone 13, iOS 27.0, over Wi-Fi, comparing stream settings (12 s each):

| Offer | Decoded fps | Received | Negotiated |
|---|---|---|---|
| Default | 45.7 | 2.20 Mbit/s | 1184×2544, max 6 Mbit/s |
| Half resolution requested | 22.5 | 1.70 Mbit/s | 1184×2544 (ignored) |
| 2 Mbit/s cap | 36.8 | 0.90 Mbit/s | max 2 Mbit/s |

With the cap in the backend, the first frame arrived after 2.7 s, the stream delivered 30 frames
per second and `home` worked. Frame rate depends on what moves on screen. Whether the cap keeps
the phone cool enough over a long Reels session is not measured yet.

Not checked: other devices or iOS versions, long sessions, unlocked use over Wi-Fi, multi-touch,
apps that use the camera, and the screenshot fallback's CoreDevice capture path.
