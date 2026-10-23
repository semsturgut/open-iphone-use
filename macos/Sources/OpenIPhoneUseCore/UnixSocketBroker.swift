import Darwin
import Foundation

public enum UnixSocketError: Error, LocalizedError {
    case invalidSocketPath
    case connectionFailed(String)
    case readFailed(String)
    case writeFailed(String)
    case malformedResponse
    case oversizedMessage

    public var errorDescription: String? {
        switch self {
        case .invalidSocketPath: "The Open iPhone Use socket path is too long."
        case .connectionFailed(let message), .readFailed(let message), .writeFailed(let message): message
        case .malformedResponse: "Open iPhone Use returned an invalid local response."
        case .oversizedMessage: "The local Open iPhone Use message exceeded the size limit."
        }
    }
}

public enum UnixSocketBrokerClient {
    public static func request(_ request: BrokerRequest, socketPath: String = OpenIPhoneUsePaths.socketPath) throws -> BrokerResponse {
        let path = Array(socketPath.utf8CString).map { UInt8(bitPattern: $0) }
        guard path.count <= MemoryLayout.size(ofValue: sockaddr_un().sun_path) else { throw UnixSocketError.invalidSocketPath }
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw UnixSocketError.connectionFailed(String(cString: strerror(errno))) }
        defer { Darwin.close(fd) }

        var address = sockaddr_un()
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { target in
            target.copyBytes(from: path.prefix(target.count))
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { throw UnixSocketError.connectionFailed("Open iPhone Use Mac is not running: \(String(cString: strerror(errno)))") }

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        var message = try encoder.encode(request)
        message.append(0x0A)
        try writeAll(fd: fd, data: message)
        let responseData = try readLine(fd: fd)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let response = try? decoder.decode(BrokerResponse.self, from: responseData) else {
            throw UnixSocketError.malformedResponse
        }
        return response
    }

    private static func writeAll(fd: Int32, data: Data) throws {
        var offset = 0
        while offset < data.count {
            let count = data.withUnsafeBytes { raw in
                Darwin.write(fd, raw.baseAddress!.advanced(by: offset), data.count - offset)
            }
            guard count > 0 else { throw UnixSocketError.writeFailed(String(cString: strerror(errno))) }
            offset += count
        }
    }

    private static func readLine(fd: Int32) throws -> Data {
        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 16_384)
        while result.count <= OpenIPhoneUseLimits.maximumBrokerMessageBytes {
            let count = Darwin.read(fd, &buffer, buffer.count)
            guard count > 0 else { throw UnixSocketError.readFailed(String(cString: strerror(errno))) }
            if let newline = buffer[..<count].firstIndex(of: 0x0A) {
                result.append(contentsOf: buffer[..<newline])
                return result
            }
            result.append(contentsOf: buffer[..<count])
        }
        throw UnixSocketError.oversizedMessage
    }
}

public final class UnixSocketBroker: @unchecked Sendable {
    private let path: String
    private let handler: @Sendable (BrokerRequest) async -> BrokerResponse
    private let lock = NSLock()
    private var descriptor: Int32 = -1
    private var acceptTask: Task<Void, Never>?

    public init(path: String = OpenIPhoneUsePaths.socketPath,
                handler: @escaping @Sendable (BrokerRequest) async -> BrokerResponse) {
        self.path = path
        self.handler = handler
    }

    public func start() throws {
        try FileManager.default.createDirectory(at: OpenIPhoneUsePaths.supportDirectory,
                                                withIntermediateDirectories: true)
        _ = chmod(OpenIPhoneUsePaths.supportDirectory.path, mode_t(S_IRWXU))
        let socketBytes = Array(path.utf8CString).map { UInt8(bitPattern: $0) }
        guard socketBytes.count <= MemoryLayout.size(ofValue: sockaddr_un().sun_path) else { throw UnixSocketError.invalidSocketPath }
        _ = unlink(path)
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw UnixSocketError.connectionFailed(String(cString: strerror(errno))) }

        var address = sockaddr_un()
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { target in
            target.copyBytes(from: socketBytes.prefix(target.count))
        }
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0, Darwin.listen(fd, 8) == 0 else {
            let message = String(cString: strerror(errno))
            Darwin.close(fd)
            throw UnixSocketError.connectionFailed(message)
        }
        _ = chmod(path, mode_t(S_IRUSR | S_IWUSR))
        lock.lock()
        descriptor = fd
        lock.unlock()
        acceptTask = Task.detached(priority: .userInitiated) { [weak self] in
            await self?.acceptLoop(fd: fd)
        }
    }

    public func stop() {
        lock.lock()
        let fd = descriptor
        descriptor = -1
        lock.unlock()
        acceptTask?.cancel()
        acceptTask = nil
        if fd >= 0 { Darwin.close(fd) }
        _ = unlink(path)
    }

    private func acceptLoop(fd: Int32) async {
        while !Task.isCancelled {
            let client = Darwin.accept(fd, nil, nil)
            if client < 0 {
                if errno == EINTR { continue }
                return
            }
            var peerUID: uid_t = 0
            var peerGID: gid_t = 0
            guard getpeereid(client, &peerUID, &peerGID) == 0, peerUID == getuid() else {
                Darwin.close(client)
                continue
            }
            Task.detached(priority: .userInitiated) { [handler] in
                await Self.handle(client, handler: handler)
            }
        }
    }

    private static func handle(_ fd: Int32, handler: @escaping @Sendable (BrokerRequest) async -> BrokerResponse) async {
        defer { Darwin.close(fd) }
        do {
            let data = try readLine(fd: fd)
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let request = try decoder.decode(BrokerRequest.self, from: data)
            let response = await handler(request)
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            var bytes = try encoder.encode(response)
            bytes.append(0x0A)
            try writeAll(fd: fd, data: bytes)
        } catch {
            let fallback = BrokerResponse(requestID: "unknown", ok: false, message: error.localizedDescription)
            if let data = try? JSONEncoder().encode(fallback) {
                try? writeAll(fd: fd, data: data + Data([0x0A]))
            }
        }
    }

    private static func readLine(fd: Int32) throws -> Data {
        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 16_384)
        while result.count <= OpenIPhoneUseLimits.maximumBrokerMessageBytes {
            let count = Darwin.read(fd, &buffer, buffer.count)
            guard count > 0 else { throw UnixSocketError.readFailed(String(cString: strerror(errno))) }
            if let newline = buffer[..<count].firstIndex(of: 0x0A) {
                result.append(contentsOf: buffer[..<newline])
                return result
            }
            result.append(contentsOf: buffer[..<count])
        }
        throw UnixSocketError.oversizedMessage
    }

    private static func writeAll(fd: Int32, data: Data) throws {
        var offset = 0
        while offset < data.count {
            let count = data.withUnsafeBytes { raw in
                Darwin.write(fd, raw.baseAddress!.advanced(by: offset), data.count - offset)
            }
            guard count > 0 else { throw UnixSocketError.writeFailed(String(cString: strerror(errno))) }
            offset += count
        }
    }
}
