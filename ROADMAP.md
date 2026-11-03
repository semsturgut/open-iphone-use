# Open iPhone Use roadmap

## Available

- [x] USB control through CoreDevice: HEVC screen stream (about 45 fps), exact touches,
      keyboard and hardware buttons, with no app on the phone.
- [x] Wi-Fi control of a phone Xcode has paired wirelessly (about 12 fps in a first test).
- [x] Loopback Web UI with a live view per phone; click to tap.
- [x] MCP server, agent skill and registration for Codex, Claude Code, Cursor, OpenCode and
      Command Code.

## Next

- [x] Several phones at once: one over USB plus any number over Wi-Fi.
- [ ] Several USB phones on one server (today: one, because the userspace tunnel is per process).
- [ ] Faster Wi-Fi start and frame rate; recover when `remoted` takes the connection back.
- [ ] Typing and clicking from the Mac keyboard and mouse in the Web UI.
- [ ] Multi-touch gestures (pinch, rotate) in MCP.
- [ ] Wider device matrix: more iPhone models, iOS 27.x releases and long sessions.
- [ ] Accessibility tree for agents, so they can tap controls by name.

For Xcode 26 / iOS 26, the Bluetooth + Safari design remains available in
[v0.0.3](https://github.com/semsturgut/open-iphone-use/releases/tag/v0.0.3).
