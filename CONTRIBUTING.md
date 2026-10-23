# Contributing

Keep changes scoped to the Mac + iPhone architecture in [README.md](README.md).
Use native Swift/SwiftUI. Preserve owner consent and inspect real UI outcomes when
claiming device behavior. Read [security](SECURITY.md) before changing capture or input.

## Mac checks

```sh
cd macos
swift test
swift build -c release
node scripts/test-capture-page.mjs
python3 scripts/test-agent-mcp.py .build/release/open-iphone-use-mcp
```

## iPhone capture checks

```sh
swiftc ios/Shared/CaptureStore.swift ios/Tests/main.swift -o /tmp/open-iphone-use-capture-check
/tmp/open-iphone-use-capture-check
xcodegen generate --spec ios/project.yml
xcodebuild -project ios/OpenIPhoneUse.xcodeproj -scheme OpenIPhoneUse -sdk iphonesimulator \
  -configuration Debug -derivedDataPath /tmp/open-iphone-use-ios-build CODE_SIGNING_ALLOWED=NO build
```

No signing team or certificate is distributed. Local device signing belongs in ignored
`ios/Config/Local.xcconfig`. Store delivery follows AGENTS.md; a development build is not
authorized publication. Do not commit credentials, capture links, local SDK paths or
personal images. A successful build, BLE delivery and actual iPhone action are separate
proofs. Include reproducible device/OS versions and remaining unknowns with a behavior change.

Contributions are licensed under Apache-2.0. Keep issues factual and respectful.
