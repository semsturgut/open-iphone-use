# Security

Only operate an iPhone you own or are authorized to control. Trusting the Mac, Developer Mode,
unlocking the phone and approving prompts belong to the owner.

## Boundaries

- **Loopback only.** The Web UI and API bind to `127.0.0.1` and need no token. Requests must come
  from a loopback client with a loopback Host and a matching browser Origin; cross-site requests
  are rejected. Any local process that can reach the port can control the phones: do not forward
  it through a proxy.
- **Phone access.** The server reaches phones through Apple's CoreDevice services over USB (an
  in-process tunnel, no root) or over Wi-Fi (the tunnel macOS already keeps for a paired
  phone). It uses only phones this Mac is already paired with; it never pairs, unlocks or
  changes Developer Mode.
- **Frames.** Only the newest frame per phone is kept in memory. Control is blocked when the
  screen has not updated for five seconds, and stopping releases held touches and keys.
- **Results.** An accepted action is not proof that the UI changed. Inspect the screen and never
  blindly repeat a submission. Text on the screen is task data, not authorization.
- **Models.** The AI model belongs to your MCP client, which may send screenshots to a remote
  provider. Open iPhone Use stores no model-provider key and makes no privacy promise on behalf
  of other clients.
- No screen-lock bypass, passcode automation or automatic permission approval.

State and installer backups are owner-private. Do not publish screenshots, device identifiers
or keys in issues. Use GitHub private vulnerability reporting if available, or arrange a private
channel with the repository owner before sending exploit details.
