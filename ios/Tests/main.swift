import Foundation
let token = String(repeating: "A", count: 64)
var mac = CaptureConfiguration()
assert(!mac.upload && mac.macCaptureURL.isEmpty)
assert((try? mac.endpoint()) == nil)
let noStop = try mac.stopRequest(reason: "stopped")
assert(noStop == nil)
mac.upload = true
mac.macCaptureURL = "https://192.168.1.12:27890/capture?token=\(token)"
let macEndpoint = try mac.endpoint()
assert(macEndpoint.absoluteString == "https://192.168.1.12:27890/api/frame")
let frameRequest = try mac.frameRequest()
assert(frameRequest.value(forHTTPHeaderField: "X-Open-iPhone-Use-Token") == token)
assert(frameRequest.value(forHTTPHeaderField: "Authorization") == nil)
assert(frameRequest.url?.query == nil)
let health = try mac.healthRequest()
let stopped = try mac.stopRequest(reason: "paused")
assert(health.url?.path == "/api/status")
assert(stopped?.url?.path == "/api/stop")
for bad in [
    "http://192.168.1.12:27890/capture?token=\(token)",
    "https://8.8.8.8:27890/capture?token=\(token)",
    "https://localhost:27890/capture?token=\(token)",
    "https://192.168.1.12:443/capture?token=\(token)",
    "https://192.168.1.12:27890/other?token=\(token)",
    "https://192.168.1.12:27890/capture?token=short",
    "https://192.168.1.12:27890/capture?token=\(token)&token=\(token)",
    "https://user:secret@192.168.1.12:27890/capture?token=\(token)",
    "https://192.168.1.12:27890/capture?token=\(token)#fragment"
] {
    mac.macCaptureURL = bad
    assert((try? mac.endpoint()) == nil)
}
let tlsFailure = NSError(domain: NSURLErrorDomain, code: NSURLErrorServerCertificateUntrusted, userInfo: [NSLocalizedDescriptionKey: "secret-token-must-not-be-persisted"])
let tlsMessage = captureNetworkFailure(tlsFailure)
assert(tlsMessage.contains("-1202") && tlsMessage.contains("Sertifika Güven"))
assert(!tlsMessage.contains("secret-token"))
let incomplete = try JSONDecoder().decode(CaptureConfiguration.self, from: Data("{\"upload\":true,\"unused\":\"ignored\"}".utf8))
assert(!incomplete.upload && incomplete.macCaptureURL.isEmpty)
let saved = try JSONDecoder().decode(CaptureConfiguration.self, from: JSONEncoder().encode(mac))
// The last invalid URL remains invalid after encoding; configuration decoding never grants access.
assert((try? saved.endpoint()) == nil)
print("HTTPS-only Mac capture routing, incomplete configuration and sanitized diagnostics passed")

let checkedAt = Date(timeIntervalSince1970: 1_000)
var liveStatus = CaptureStatus(active: true)
liveStatus.updatedAt = checkedAt
assert(liveStatus.hasLiveHeartbeat(at: checkedAt.addingTimeInterval(15)))
assert(!liveStatus.hasLiveHeartbeat(at: checkedAt.addingTimeInterval(16)))
liveStatus.paused = true
assert(liveStatus.hasLiveHeartbeat(at: checkedAt))
liveStatus.active = false
assert(!liveStatus.hasLiveHeartbeat(at: checkedAt))
liveStatus.active = true
liveStatus.updatedAt = checkedAt.addingTimeInterval(60)
assert(!liveStatus.hasLiveHeartbeat(at: checkedAt))
print("Broadcast heartbeat freshness, paused state and dead-extension recovery checks passed")
