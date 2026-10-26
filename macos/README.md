# Native macOS helper

Install and build the CLI runtime from the repository root:

```sh
sh setup.sh
open-iphone-use serve
open-iphone-use new
open-iphone-use stop
```

The installer builds `open-iphone-use-headless`, bundles its Bluetooth purpose strings
into the executable, and installs it next to the Python runtime. There is no application
bundle or disk-image distribution. The loopback browser UI is served by the CLI.

`HeadlessRadio` publishes the full 128-bit HID service UUID, maintains separate central
subscriptions/report state and queues, and uses explicit `onSubscribedCentrals` targets.
Readiness-driven retries are fair across centrals; cancellation is scoped to a peer.
Bluetooth names use exact-UUID peripheral cache/GAP lookup, with generic labels treated as
fallbacks. A cached `iPhone` does not prevent trying GAP Device Name.

Development checks (no signing or release upload):

```sh
swift test --package-path macos
node macos/scripts/test-capture-page.mjs
python3 macos/scripts/test-agent-mcp.py macos/.build/debug/open-iphone-use-mcp
PYTHONPATH=server .venv/bin/python -m unittest discover -s server/tests -v
```

The Swift stdio MCP executable remains as a compatibility test surface. New installations
register the portable Python CLI MCP server and its skill. Core controller/vision tests
remain useful without the retired desktop entry point. On restricted build hosts,
SwiftPM may need `--disable-sandbox --cache-path /tmp/oiu-swift-cache`; this does not
change Bluetooth permission or establish real iPhone behavior.
