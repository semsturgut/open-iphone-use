import Foundation
import Logging
import MCP
import OpenIPhoneUseCore

/// Keep SDK framing and backpressure; adapt only unsupported initialization metadata.
actor CompatibleStdioTransport: Transport {
    private let base: StdioTransport
    nonisolated let logger: Logger

    init() {
        let base = StdioTransport()
        self.base = base
        logger = base.logger
    }

    func connect() async throws { try await base.connect() }
    func disconnect() async { await base.disconnect() }
    func send(_ data: Data) async throws { try await base.send(data) }

    func receive() -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await data in await base.receive() {
                        continuation.yield(MCPHandshakeCompatibility.normalize(data))
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
