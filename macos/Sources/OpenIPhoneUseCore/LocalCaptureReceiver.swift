@preconcurrency import Network
import Foundation
import Security

@MainActor
public final class LocalCaptureReceiver {
    public static let capturePort: UInt16 = 27_890

    var onCalibrationRequest: ((Bool) async -> BrokerResponse?)?
    private var calibrationRequestInFlight = false
    private let frames: FrameStore
    private let queue = DispatchQueue(label: "dev.openiphoneuse.capture", qos: .userInitiated)
    private var tlsListener: NWListener?
    private var token: String?
    private var interface: PrivateIPv4Interface?
    private var page: Data?
    /// Capture rate the page should throttle itself to, published with every frame.
    var framesPerSecond = 1
    /// Board request the page obeys; the epoch changes only when the Mac wants a change.
    var boardEpoch = 1
    var boardOpen = false
    /// Hash of the served page. A page that sees a different value reloads itself, so an
    /// updated script takes effect without asking the owner to reload by hand.
    var pageVersion = ""

    private var onStateChange: ((String, String?, String?) -> Void)?

    public init(frames: FrameStore) { self.frames = frames }

    public func start(onStateChange: @escaping (String, String?, String?) -> Void) throws {
        guard tlsListener == nil else { return }
        self.onStateChange = onStateChange
        guard let interface = PrivateIPv4Interface.activeWiFi() else { throw LocalCertificateError.noPrivateIPv4 }
        self.interface = interface
        let identity = try LocalCertificateManager.makeIdentity(for: interface.address)
        guard let secIdentity = sec_identity_create(identity.identity) else { throw LocalCertificateError.identityMissing }

        let tls = NWProtocolTLS.Options()
        sec_protocol_options_set_min_tls_protocol_version(tls.securityProtocolOptions, .TLSv12)
        sec_protocol_options_set_local_identity(tls.securityProtocolOptions, secIdentity)
        let tlsParameters = NWParameters(tls: tls, tcp: NWProtocolTCP.Options())
        tlsParameters.requiredInterfaceType = .wifi
        let tlsPort = NWEndpoint.Port(rawValue: Self.capturePort)!
        let secure = try NWListener(using: tlsParameters, on: tlsPort)
        secure.newConnectionHandler = { [weak self] connection in
            connection.start(queue: self?.queue ?? .main)
            Self.receive(connection) { [weak self] request in
                Task { @MainActor in await self?.respond(to: request, on: connection) }
            }
        }
        secure.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                switch state {
                case .ready:
                    self?.onStateChange?("running", "https://\(interface.address):\(Self.capturePort)/capture?token=\(self?.token ?? "")", nil)
                case .failed(let error): self?.fail(error.localizedDescription)
                case .cancelled: break
                default: break
                }
            }
        }

        // Keep the session token stable across receiver restarts so the iPhone app's
        // saved capture URL keeps working without re-pasting it over USB each time.
        let tokenKey = "OpenIPhoneUseCaptureToken"
        if let saved = UserDefaults.standard.string(forKey: tokenKey), saved.count == 64 {
            token = saved
        } else {
            let fresh = UUID().uuidString.replacingOccurrences(of: "-", with: "") + UUID().uuidString.replacingOccurrences(of: "-", with: "")
            UserDefaults.standard.set(fresh, forKey: tokenKey)
            token = fresh
        }
        page = try Self.loadCapturePage()
        pageVersion = page.map(Self.version(of:)) ?? ""
        tlsListener = secure
        secure.start(queue: queue)
        Task { await frames.setCaptureActive(false) }
        onStateChange("starting", "https://\(interface.address):\(Self.capturePort)/capture?token=\(token!)", nil)
    }

    public func stop() {
        tlsListener?.cancel()
        tlsListener = nil
        page = nil
        onStateChange?("stopped", nil, nil)
        Task { await frames.setCaptureActive(false) }
    }

    private func fail(_ message: String) {
        onStateChange?("error", nil, message)
        Task { await frames.setCaptureActive(false, error: message) }
    }

    private func respond(to request: HTTPRequest, on connection: NWConnection) async {
        guard let interface, let remoteIPv4 = request.remoteIPv4, interface.contains(remoteIPv4) else {
            Self.send(status: 403, reason: "Forbidden", mime: "text/plain", body: Data("Private Wi-Fi only".utf8), on: connection)
            return
        }

        if request.method == "GET", request.path == "/capture" {
            guard tokenMatches(request.query["token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "text/plain", body: Data("Open the capture URL shown in Open iPhone Use on Mac.".utf8), on: connection)
                return
            }
            Self.send(status: 200, reason: "OK", mime: "text/html; charset=utf-8", body: page ?? Data(),
                      extraHeaders: ["Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:",
                                     "Referrer-Policy": "no-referrer"], on: connection)
            return
        }

        if request.method == "POST", request.path == "/api/frame" {
            guard tokenMatches(request.headers["x-open-iphone-use-token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "application/json", body: Data("{\"error\":\"unauthorized\"}".utf8), on: connection)
                return
            }
            do {
                let frame = try await frames.put(jpeg: request.body)
                await frames.setPageReport(request.headers["x-open-iphone-use-page"])
                let object: [String: Any] = ["frameID": frame.frameID,
                                             "capturedAt": ISO8601DateFormatter().string(from: frame.capturedAt),
                                             "fps": framesPerSecond,
                                             "boardEpoch": boardEpoch,
                                             "boardOpen": boardOpen,
                                             "pageVersion": pageVersion]
                let body = try JSONSerialization.data(withJSONObject: object)
                Self.send(status: 200, reason: "OK", mime: "application/json", body: body, on: connection)
            } catch {
                let code = (error as? FrameStoreError) == .tooLarge ? 413 : 400
                Self.send(status: code, reason: code == 413 ? "Payload Too Large" : "Bad Request", mime: "application/json",
                          body: Data("{\"error\":\"invalid_frame\"}".utf8), on: connection)
            }
            return
        }

        if request.method == "POST", request.path == "/api/stop" {
            guard tokenMatches(request.headers["x-open-iphone-use-token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "application/json", body: Data("{\"error\":\"unauthorized\"}".utf8), on: connection)
                return
            }
            let payload = (try? JSONSerialization.jsonObject(with: request.body)) as? [String: String]
            let reason = payload?["reason"]
            let message: String
            switch reason {
            case "background": message = "Safari is in the background. Use the native iPhone Open iPhone Use broadcast to capture other apps."
            case "muted": message = "Safari stopped delivering video frames. Resume sharing or use the native iPhone broadcast."
            case "paused": message = "iOS paused the screen broadcast. Resume it on the iPhone."
            default: message = "Screen sharing stopped on the iPhone. Start a new broadcast to resume."
            }
            await frames.setCaptureActive(false, error: message)
            Self.send(status: 200, reason: "OK", mime: "application/json", body: Data("{\"stopped\":true}".utf8), on: connection)
            return
        }

        if request.method == "POST", request.path == "/api/calibrate" {
            guard tokenMatches(request.headers["x-open-iphone-use-token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "application/json",
                          body: Data("{}".utf8), on: connection)
                return
            }
            guard !calibrationRequestInFlight else {
                Self.send(status: 409, reason: "Conflict", mime: "application/json",
                          body: Data("{}".utf8), on: connection)
                return
            }
            calibrationRequestInFlight = true
            defer { calibrationRequestInFlight = false }
            let object = (try? JSONSerialization.jsonObject(with: request.body)) as? [String: Bool]
            let result = await onCalibrationRequest?(object?["force"] == true)
            let body = (try? JSONSerialization.data(withJSONObject:
                ["ok": result?.ok ?? false, "message": result?.message ?? "Mac controller unavailable"] as [String: Any])) ?? Data()
            Self.send(status: 200, reason: "OK", mime: "application/json", body: body, on: connection)
            return
        }

        if request.method == "POST", request.path == "/api/tap" {
            guard tokenMatches(request.headers["x-open-iphone-use-token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "application/json", body: Data("{\"error\":\"unauthorized\"}".utf8), on: connection)
                return
            }
            if let object = (try? JSONSerialization.jsonObject(with: request.body)) as? [String: Double],
               let x = object["x"], let y = object["y"] {
                await frames.recordTap(x: x, y: y)
                Self.send(status: 200, reason: "OK", mime: "application/json", body: Data("{\"recorded\":true}".utf8), on: connection)
            } else {
                Self.send(status: 400, reason: "Bad Request", mime: "application/json", body: Data("{\"error\":\"invalid_tap\"}".utf8), on: connection)
            }
            return
        }

        if request.method == "GET", request.path == "/api/status" {
            guard tokenMatches(request.headers["x-open-iphone-use-token"]) else {
                Self.send(status: 401, reason: "Unauthorized", mime: "application/json", body: Data("{\"error\":\"unauthorized\"}".utf8), on: connection)
                return
            }
            let latest = await frames.latestFrame()
            let age: Int
            if let latest { age = await frames.ageMilliseconds(of: latest) }
            else { age = -1 }
            let active = await frames.isCaptureActive()
            let status: [String: Any] = ["active": active && age >= 0 && age <= 5_000,
                                         "frameAgeMilliseconds": age]
            let body = (try? JSONSerialization.data(withJSONObject: status)) ?? Data("{}".utf8)
            Self.send(status: 200, reason: "OK", mime: "application/json", body: body, on: connection)
            return
        }

        Self.send(status: 404, reason: "Not Found", mime: "text/plain", body: Data("Not found".utf8), on: connection)
    }

    private func tokenMatches(_ candidate: String?) -> Bool {
        guard let token, let candidate, candidate.utf8.count == token.utf8.count else { return false }
        var difference: UInt8 = 0
        for (lhs, rhs) in zip(token.utf8, candidate.utf8) { difference |= lhs ^ rhs }
        return difference == 0
    }

    nonisolated fileprivate static func send(status: Int, reason: String, mime: String, body: Data,
                                             extraHeaders: [String: String] = [:], on connection: NWConnection) {
        var headers = ["HTTP/1.1 \(status) \(reason)", "Content-Type: \(mime)",
                       "Content-Length: \(body.count)", "Connection: close", "Cache-Control: no-store"]
        for (name, value) in extraHeaders { headers.append("\(name): \(value)") }
        let head = Data((headers.joined(separator: "\r\n") + "\r\n\r\n").utf8)
        connection.send(content: head + body, completion: .contentProcessed { _ in connection.cancel() })
    }

    nonisolated private static func receive(_ connection: NWConnection,
                                            completion: @escaping @Sendable (HTTPRequest) -> Void) {
        HTTPReadLoop(connection: connection, completion: completion).next()
    }

    /// FNV-1a over the page bytes: short, and changes exactly when the page changes.
    static func version(of data: Data) -> String {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in data {
            hash ^= UInt64(byte)
            hash = hash &* 0x100000001b3
        }
        return String(hash, radix: 16)
    }

    private static func loadCapturePage() throws -> Data {
        let candidates = [
            Bundle.main.resourceURL?.appendingPathComponent("OpenIPhoneUseMac_OpenIPhoneUseCore.bundle/capture.html"),
            Bundle.main.bundleURL.appendingPathComponent("OpenIPhoneUseMac_OpenIPhoneUseCore.bundle/capture.html"),
            Bundle.main.resourceURL?.appendingPathComponent("OpenIPhoneUseMac_OpenIPhoneUseCore.bundle/Resources/capture.html"),
            Bundle.main.bundleURL.appendingPathComponent("OpenIPhoneUseMac_OpenIPhoneUseCore.bundle/Resources/capture.html"),
        ].compactMap { $0 }
        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return try Data(contentsOf: url)
        }
        throw CocoaError(.fileReadNoSuchFile)
    }
}

private struct HTTPRequest: Sendable {
    enum ParseResult: Sendable { case incomplete, invalid(Int), complete(HTTPRequest) }
    let method: String
    let path: String
    let query: [String: String]
    let headers: [String: String]
    let body: Data
    let remoteIPv4: String?

    static func parse(_ data: Data, remote: NWEndpoint) -> ParseResult {
        let separator = Data([13, 10, 13, 10])
        guard let range = data.range(of: separator) else { return data.count > FrameStore.maximumFrameBytes ? .invalid(413) : .incomplete }
        let headerData = data[..<range.lowerBound]
        guard let headerText = String(data: headerData, encoding: .utf8) else { return .invalid(400) }
        let lines = headerText.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else { return .invalid(400) }
        let requestParts = requestLine.split(separator: " ")
        guard requestParts.count == 3 else { return .invalid(400) }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[String(line[..<colon]).lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let contentLength = Int(headers["content-length"] ?? "0") ?? -1
        guard contentLength >= 0 else { return .invalid(400) }
        guard contentLength <= FrameStore.maximumFrameBytes else { return .invalid(413) }
        let bodyStart = range.upperBound
        guard data.count >= bodyStart + contentLength else { return .incomplete }
        guard let components = URLComponents(string: "http://local\(requestParts[1])") else { return .invalid(400) }
        let query = Dictionary(components.queryItems?.map { ($0.name, $0.value ?? "") } ?? [], uniquingKeysWith: { first, _ in first })
        var host: String?
        if case .hostPort(let remoteHost, _) = remote { host = remoteHost.privateIPv4Address }
        return .complete(HTTPRequest(method: String(requestParts[0]), path: components.path, query: query,
                                     headers: headers, body: data.subdata(in: bodyStart..<(bodyStart + contentLength)), remoteIPv4: host))
    }
}

private final class HTTPReadLoop: @unchecked Sendable {
    private let connection: NWConnection
    private let completion: @Sendable (HTTPRequest) -> Void
    private let lock = NSLock()
    private var collected = Data()

    init(connection: NWConnection, completion: @escaping @Sendable (HTTPRequest) -> Void) {
        self.connection = connection
        self.completion = completion
    }

    func next() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [self] data, _, complete, error in
            if let data { self.append(data) }
            if error != nil || complete && data == nil { self.connection.cancel(); return }
            let bytes = self.snapshot()
            switch HTTPRequest.parse(bytes, remote: self.connection.endpoint) {
            case .complete(let request): self.completion(request)
            case .invalid(let status):
                LocalCaptureReceiver.send(status: status,
                                          reason: status == 413 ? "Payload Too Large" : "Bad Request",
                                          mime: "text/plain", body: Data("Invalid request".utf8),
                                          on: self.connection)
            case .incomplete:
                if bytes.count > FrameStore.maximumFrameBytes + 65_536 { self.connection.cancel() }
                else { self.next() }
            }
        }
    }

    private func append(_ data: Data) {
        lock.lock()
        collected.append(data)
        lock.unlock()
    }

    private func snapshot() -> Data {
        lock.lock()
        defer { lock.unlock() }
        return collected
    }
}
