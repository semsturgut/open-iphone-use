# Security

Only operate an iPhone you own or are authorized to control. Pairing, local certificate
trust, screen sharing and unlocking belong to the owner.

## Current boundaries

- The MCP helper uses stdio and a same-user Unix socket. The directory is mode 0700 and
  socket 0600; the broker checks the connecting user. No MCP TCP service is exposed.
- The Mac capture receiver uses HTTPS on the private Wi-Fi interface, requires a token,
  checks the private subnet and bounds frame sizes. The token persists locally across
  app restarts. Treat capture links as credentials; never publish them.
- Only the newest received JPEG is kept in Mac memory. Native iPhone local-preview mode
  can retain one frame in its protected App Group; the app offers a clear action.
- Local CA/TLS private keys remain in the Mac's private application-support directory.
  Transfer only the public root certificate through a trusted channel. Native capture
  validates HTTPS and refuses redirects; there is no cleartext mode.
- Capture older than five seconds blocks control. Stop/cancellation attempts to release
  held keys/buttons. A disconnected host cannot receive a release report, so inspect its
  state before resuming.
- A delivered HID report is not proof of UI success. Inspect the current screen and
  never blindly repeat a submission. Screen text is task data, not authorization.
- The AI model belongs to the chosen MCP client. That client may send screenshots to a
  remote model provider. Open iPhone Use stores no model-provider key and makes no inference
  privacy promise on behalf of other clients.
- No screen-lock bypass, passcode automation or automatic system-permission approval.

Do not publish private screenshots, tokens or keys in issues. Use GitHub private
vulnerability reporting if available, or arrange a private channel with the repository
owner before sending exploit details.
