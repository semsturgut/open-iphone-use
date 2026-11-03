# Legacy Bluetooth setup (Xcode 26 / iOS 26)

Before USB control, Open iPhone Use drove phones with Bluetooth HID through AssistiveTouch and
received the screen from Safari screen sharing. Use this setup when your Mac cannot run
Xcode 27 or your iPhone is not on iOS 27. Install the last release built around it:

```sh
curl -fsSL https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/install.sh | bash -s -- --version v0.0.3
```

Newer versions keep the same path behind `open-iphone-use serve --bluetooth`; add phones with
`open-iphone-use new --bt PEER_ID` (`open-iphone-use bluetooth` lists peers).

## Pair and share each iPhone

1. Unlock the iPhone, enable AssistiveTouch, then pair the host under **Settings →
   Accessibility → Touch → AssistiveTouch → Devices → Bluetooth Devices**. Approve the
   owner's pairing prompts. Both keyboard and pointer HID subscriptions must be connected.
2. Choose the connected phone in the Web UI or run `open-iphone-use new --bt PEER_ID`
   (`add` is an alias); `open-iphone-use bluetooth` lists candidates. On v0.0.3, plain
   `open-iphone-use new` chooses interactively. Bluetooth supplies owner device names where available; generic or duplicate names
   get fallbacks. Do not infer identity from a name.
3. Each phone gets a private sharing link and QR. Use Copy or, where supported, Share and
   the system share sheet (including AirDrop where available). On first use install the
   public CA on the phone, verify its SHA-256 fingerprint against the terminal, and enable
   trust in **Settings → General → About → Certificate Trust Settings**. Open that phone's
   link in Safari and approve **Start sharing**. Repeat for each phone.

The capture page requests video at an ideal **30 fps**; actual delivery depends on the
phone and network. Capture links are credentials: keep them private. Only the owner pairs,
trusts certificates, unlocks the phone and approves sharing. Bluetooth and fresh screen
sharing are both required for control.

```sh
open-iphone-use devices                    # list registered phones
open-iphone-use status --device DEVICE_ID  # inspect a phone
open-iphone-use link DEVICE_ID             # show its link and QR again
open-iphone-use remove DEVICE_ID           # remove phone and revoke its link
open-iphone-use stop
```

`remove --device DEVICE_ID` is also supported. Removal cancels that phone's input and clears
its frames and calibration; it does not remove its OS Bluetooth bond. See
[headless server](docs/headless-server.md) for reconnect and network details.

## Pointer calibration

The default auto-hiding AssistiveTouch pointer is supported. With Safari sharing visible,
preparation measures seven samples on its calibration board and fits the visual pointer;
a good fit is accepted immediately, while a poor fit is remeasured once. Compatible
calibration is reused. Explicitly recalibrate with `ensure_session` or `prepare_session`
using `recalibrate=true`, or Safari's **Calibrate pointer** button. After a failed automatic
attempt, another automatic attempt can be made after three minutes; a failed explicit attempt
preserves the stored mapping. Keep pointer appearance, tracking speed, Zoom and orientation
stable.

Coordinate taps use bounded corrections and learn consistent landing offsets. Results report
whether the landing was visually verified, observed without verification, predicted, or blind;
predicted and blind results are unverified. A tap is refused when an observed landing is over 3% off target, a hidden
corrected pointer is predicted over 1% off, or an unseen pointer has untrusted calibration.
Even a verified landing does not prove the intended UI changed: inspect the screen after
**every action**.

## Platforms

macOS has been tested with an iPhone. Linux/BlueZ and Windows/WinRT backends exist and should
work in principle, but have **not been tested on real hardware**; reports and pull requests
are welcome. On macOS, two iPhones have been shared and connected at the same time, and a
paired iPhone reconnected over Bluetooth within a few seconds after a server restart. See the
[validation record](docs/web-server-validation.md) for what was actually tested.

