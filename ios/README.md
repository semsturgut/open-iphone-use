# Native iPhone capture

An optional SwiftUI app and ReplayKit Broadcast Upload Extension for sharing the iPhone
screen directly with Open iPhone Use on Mac. iOS 18 minimum; no third-party SDKs. Input is
provided by the Mac over Bluetooth. The Safari sharing page remains the default demo.

## Build

With Xcode and XcodeGen installed:

```sh
xcodegen generate --spec ios/project.yml
xcodebuild -project ios/OpenIPhoneUse.xcodeproj -scheme OpenIPhoneUse -sdk iphonesimulator \
  -configuration Debug -derivedDataPath /tmp/open-iphone-use-ios-build CODE_SIGNING_ALLOWED=NO build
```

For your own physical development build, copy `Config/Local.xcconfig.example` to ignored
`Config/Local.xcconfig`, enter your development team and unique bundle/App Group IDs,
and regenerate. Configure the same App Group for both targets. No team, provisioning
profile, signing credential or private key is distributed. Store delivery follows AGENTS.md.

## Share with the Mac

1. Open Mac Open iPhone Use on the same private Wi-Fi as the iPhone.
2. Install/trust its public root CA on iPhone through an owner-approved transfer. The
   native client validates TLS and will not bypass certificate errors.
3. Enable **Ekran karelerini gönder** and paste the Mac app's complete HTTPS capture link.
4. Check the receiver connection, save, then approve iOS's system broadcast picker.
5. Switch to a harmless app and verify its screen in the Mac live preview.
6. Stop through iOS's broadcast indicator. The in-app stop request is best effort.

No separate session ID or AI key is entered. Local-preview mode keeps only `latest.jpg`
in the protected App Group; upload mode stores no local preview. Configuration contains
the private capture URL and uses complete file protection. Clear it through the app.

## Capture behavior

The extension discards audio, applies orientation, scales JPEGs to a maximum 1024-pixel
longest side and caps them at 512 KiB. It sends at most one frame per second with one
request in flight, using ephemeral URLSession and a 10-second request timeout. Redirects
are refused. Authentication failures stop the broadcast; transient network failures can
retry on a later frame. Pause/stop notifications to Mac are best effort.

A two-second status heartbeat keeps the UI responsive after extension termination. A
saved active flag older than 15 seconds is marked uncertain, allowing configuration
recovery without pretending that iOS stopped broadcasting. Unlock and system consent
remain owner actions. Protected screens, lock, rotation and long-running stability need
independent physical checks.

Debug supports `--check-receiver` for a foreground connection check only. Diagnostics
persist domain/code rather than headers, URLs or NSError userInfo. Both build modes use
the same HTTPS-only configuration. Check the shared configuration logic with:

```sh
swiftc ios/Shared/CaptureStore.swift ios/Tests/main.swift -o /tmp/open-iphone-use-capture-check
/tmp/open-iphone-use-capture-check
```

See [architecture](../docs/architecture.md), [Mac setup](../macos/README.md) and
[physical evidence](../docs/device-matrix.md).
