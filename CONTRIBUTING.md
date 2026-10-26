# Contributing

Follow the headless and standalone architectures in [README.md](README.md).
Keep macOS/iOS native Swift; portable service code belongs in `server/`. Preserve owner consent and inspect real UI outcomes when
claiming device behavior. Read [security](SECURITY.md) before changing capture or input.

## Portable server checks

Install development dependencies with `python3 -m venv .venv` and `.venv/bin/python -m pip install ./server`.
For BlueZ interface/channel tests on macOS, also install `dbus-fast>=5.2,<6` in that test environment; it does not access a Linux system bus merely by importing the interfaces.

```sh
PYTHONPATH=server .venv/bin/python -m unittest discover -s server/tests -v
node --check server/oiu/web/app.js
```

Run `sh -n setup.sh` and `shellcheck setup.sh` when available. Check `setup.ps1` with the
PowerShell parser and `Invoke-ScriptAnalyzer` (ShellCheck does not support PowerShell).
Windows execution and GATT behavior still need real host tests; static/contract checks
cannot prove WinRT permissions or driver support. Installer checks must use temporary
home/prefix directories, preserving real client configs.

To exercise installed CLI startup with automatic HTTPS and the real host radio (without
sending physical input), build/copy the helper into the test environment and run:

```sh
sh setup.sh --no-agents  # use temporary install/bin roots for isolated installer checks
.venv/bin/python server/tests/serve_smoke.py .venv/bin/open-iphone-use
```

The smoke reports the radio capability separately from HTTP/enrollment success. See
[the validation record](docs/web-server-validation.md) for tested and untested behavior.

The packaged capture page mirrors the native source; its equality test must pass after
any edit. To check two real native helper processes without Bluetooth advertising:

```sh
swift build --package-path macos -c release
PYTHONPATH=server python3 server/tests/native_smoke.py macos/.build/release/open-iphone-use-headless
```

## Mac checks

```sh
cd macos
swift test
node scripts/test-capture-page.mjs
python3 scripts/test-agent-mcp.py .build/debug/open-iphone-use-mcp
```

## Historical iPhone capture checks (only when an ios/ tree is present)

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
