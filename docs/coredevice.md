# CoreDevice control (experimental)

A USB phone can be controlled without Bluetooth HID, AssistiveTouch or Safari sharing. The
server opens Xcode's CoreDevice services over a rootless, in-process RSD tunnel
([pymobiledevice3](https://github.com/doronz88/pymobiledevice3)'s userspace tunnel):

- **Screen:** CoreDevice screen capture, or the DVT instruments screenshot service with older
  Developer Disk Images. Frames are pulled from the phone; no capture link is needed.
- **Input:** dtuhidd's universal HID service, the plumbing behind Xcode's remote device view.
  Touches are absolute (`0…65535` across the display), so there is no pointer, calibration or
  landing correction. Text and keys use a host-registered virtual keyboard.

## Requirements

- macOS host with the optional dependency: `pip install "open-iphone-use-server[coredevice]"`.
- iPhone connected by USB, trusted by this Mac, with **Developer Mode** on.
- For touch and keyboard: a Developer Disk Image that ships the HID service (Xcode 27 or later)
  and **Settings › Developer › UI Automation** enabled on the phone. iOS publishes
  `com.apple.coredevice.hid.universalhidservice` only then. Screenshots work without it.
- One CoreDevice phone per server process: the userspace network stack is process-global.

## Use

```sh
open-iphone-use coredevices                # USB phones by UDID
open-iphone-use new --udid <UDID>          # add one; no link or QR
```

MCP clients call `list_coredevice_candidates`, then `create_device` with `udid`. Every other
tool is unchanged. `ensure_session` checks that HID is available instead of calibrating;
`move_pointer` (relative motion) is refused; `pointer_move` sets the target used by `click`.
`stop_control` cancels the running action and releases the finger and keys.

## Validation record

Measured on an iPhone 13 (iPhone14,5) on iOS 27.0 over USB, with the Xcode 26.2 Developer Disk
Image mounted:

- The rootless tunnel connected in about 1.5 s; screenshots arrived through the DVT service at
  about 1.4 frames per second at full resolution (1170×2532).
- The phone did not publish the display or universal HID services with that image, and the
  backend reported the requirement instead of sending input.

Touch, keyboard and the CoreDevice screen capture path have **not** been verified on hardware.
Report results with an Xcode 27 image and UI Automation enabled.
