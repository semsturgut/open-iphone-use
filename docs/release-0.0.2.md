# Open iPhone Use 0.0.2

This update keeps iPhone screen sharing video-only. The capture page no longer requests
display audio or falls back to the microphone, avoiding microphone capture that can
interfere with autoplay in other apps. The inaudible WebAudio keepalive remains in place
for Safari background sharing.

## Assets

- `Open-iPhone-Use-0.0.2-macos-arm64.dmg` — Apple Silicon development-signed build.
- Matching `.sha256` checksum.

The DMG is not Developer ID notarized. Background screen-sharing behavior should be
verified on the target iPhone/iOS version after installation.
