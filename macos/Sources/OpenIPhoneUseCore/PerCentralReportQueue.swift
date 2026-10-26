import Foundation

/// Independent FIFO/backpressure and last-report state for each subscribed central.
/// The host's readiness callback is global; each retry still names one concrete peer.
@MainActor
public final class PerCentralReportQueue {
    private struct Pending {
        let id: UUID
        let report: Int
        let payload: Data
        let continuation: CheckedContinuation<Void, any Error>
    }
    private var queues: [String: [Pending]] = [:]
    private var values: [String: [Int: Data]] = [:]
    private let deliver: (String, Int, Data) -> Bool
    private let timeout: Duration
    private var lastServed: String?
    public init(timeout: Duration = .seconds(3), deliver: @escaping (String, Int, Data) -> Bool) {
        self.deliver = deliver; self.timeout = timeout
    }
    public func value(peer: String, report: Int) -> Data {
        values[peer]?[report] ?? Data(repeating: 0, count: report == 1 ? 8 : 4)
    }
    public func send(peer: String, report: Int, payload: Data) async throws {
        let id = UUID()
        try Task.checkCancellation()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                guard !Task.isCancelled else { continuation.resume(throwing: CancellationError()); return }
                guard queues[peer, default: []].count < 32 else {
                    continuation.resume(throwing: QueueError.full); return
                }
                queues[peer, default: []].append(Pending(id: id, report: report, payload: payload, continuation: continuation))
                Task { @MainActor [weak self, timeout] in
                    try? await Task.sleep(for: timeout)
                    self?.fail(peer: peer, id: id, error: QueueError.timeout)
                }
                ready()
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.fail(peer: peer, id: id, error: CancellationError()) }
        }
    }
    public func ready() {
        // Round robin: a blocked central never prevents attempting another central.
        var progress = true
        while progress {
            progress = false
            let peers = queues.keys.sorted()
            let start = peers.firstIndex(where: { $0 > (lastServed ?? "") }) ?? 0
            for peer in Array(peers.dropFirst(start)) + Array(peers.prefix(start)) {
                guard let first = queues[peer]?.first, deliver(peer, first.report, first.payload) else { continue }
                queues[peer]?.removeFirst()
                values[peer, default: [:]][first.report] = first.payload
                lastServed = peer
                first.continuation.resume()
                progress = true
            }
        }
    }
    public func cancel(peer: String, forget: Bool = false) {
        let pending = queues.removeValue(forKey: peer) ?? []
        if forget { values.removeValue(forKey: peer) }
        for entry in pending { entry.continuation.resume(throwing: CancellationError()) }
    }
    private func fail(peer: String, id: UUID, error: any Error) {
        guard let index = queues[peer]?.firstIndex(where: { $0.id == id }), let entry = queues[peer]?.remove(at: index) else { return }
        entry.continuation.resume(throwing: error)
    }
    private enum QueueError: LocalizedError {
        case full, timeout
        var errorDescription: String? {
            switch self {
            case .full: "Per-phone HID queue full"
            case .timeout: "Bluetooth report backpressure timeout for this phone"
            }
        }
    }
}
