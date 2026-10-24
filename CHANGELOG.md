# Changelog

## 0.0.2 — experimental alpha

- Fix iPhone screen capture requesting microphone audio: capture is now video-only, with no
  microphone fallback, so sharing does not interfere with autoplay in other apps.
- Keep the inaudible WebAudio session used as Safari's background keepalive.

## 0.0.1 — experimental alpha

- Native Mac app with onboarding, live iPhone preview and private capture-link masking.
- Bluetooth HID keyboard, relative pointer and press/drag/release gestures.
- Local HTTPS Safari sharing and optional source-build ReplayKit app.
- Bundled standard MCP server with inspect/action/inspect workflow.
- Two-click calibration, compatible mapping reuse and stale-capture guards.
- Distinct list scrolling and Reels/Shorts navigation.
- Apple Silicon DMG, screenshots and a real-device demo.

The DMG is development signed, not Developer ID notarized. Other Mac/iPhone
combinations need physical checks. Linux/Windows and multiple iPhones are planned.
