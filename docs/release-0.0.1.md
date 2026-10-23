# Open iPhone Use 0.0.1

First experimental Mac + iPhone release. The Mac advertises as a Bluetooth keyboard and
mouse; iPhone frames arrive over private Wi-Fi HTTPS. Connect the bundled stdio MCP
server to your image-capable agent.

## Assets

- `Open-iPhone-Use-0.0.1-macos-arm64.dmg` — Apple Silicon development-signed build.
- Matching `.sha256` checksum.
- `open-iphone-use-instagram-demo.mov` — real Mac window / paired iPhone demo.

Drag Open iPhone Use.app into Applications, then follow the README setup steps.
Owner-approved Bluetooth, AssistiveTouch, certificate trust and sharing are required.

## Known boundaries

Not Developer ID notarized. Clean installation on another Mac and other phones has not
been established. The bridge stores no model key; your MCP client owns inference and
can send screenshots to its provider. Only one paired phone is supported. Linux/Windows
and multiple iPhones are planned. Pointer/text/OS limits are listed in the README.

The repository begins with a new initial commit. Private development history, local
receiver credentials, signing configuration and private keys are excluded.
