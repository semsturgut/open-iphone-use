# Architecture

```mermaid
flowchart LR
    P[iPhone, iOS 27] -->|HEVC screen stream| T[RSD tunnel: USB userspace or Wi-Fi via remoted]
    T --> B[CoreDeviceBackend: VideoToolbox decode, HID]
    B --> R[Registry: phones, frames, action queues]
    R --> W[Loopback Web UI + API, live MJPEG]
    A[MCP client] --> C[stdio CLI] --> W
    B -->|touches, keys, buttons| T
```

- **`coredevice.py`** opens one tunnel per phone: over USB, pymobiledevice3's in-process
  userspace tunnel (no root); over Wi-Fi, the tunnel macOS's `remoted` already keeps for a
  paired phone. It starts the display service's HEVC stream, reuses pymobiledevice3's VNC
  pipeline (RTP depacketizing, VideoToolbox decode, keyframe recovery and the RTCP keepalive)
  and turns frames into JPEGs. Touches and keys go to dtuhidd's universal HID service, which
  only accepts input while that stream runs; buttons go through Indigo HID.
- **`accessibility.py`** opens a separate DTX subscription on the existing RSD connection,
  admits matching InitialDump and AdditionalData trees, decodes inert keyed archives, and
  checks refs with live attributes and hit testing before entering the HID input path.
- **`frames.py`** formats full, 640-pixel preview or metadata-only responses and hashes the
  native JPEG. Capture and coordinate fractions retain their existing representation.
- **`core.py`** keeps each phone's latest frame and a frame-ID history, and serializes actions
  per phone. Input needs a frame captured after the phone's last input, at most 120 seconds
  old, while the screen is live. Ref actions perform live accessibility admission. A batch
  holds the same queue lock for all steps and stops at its first failure.
- **`http.py`** serves the Web UI, the JSON API and a live MJPEG stream per phone on loopback.
- **`mcp.py`** is a stdio MCP server that calls that API.

One USB phone per server process: pymobiledevice3's userspace network stack is process-global.
Wi-Fi phones ride `remoted`'s tunnels and can be added alongside it; their video goes to a host
kernel socket, while the USB phone's goes to the userspace stack.
