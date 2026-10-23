import Foundation

struct HIDInputReport: Equatable, Sendable {
    let reportID: UInt8
    let payload: Data
}

/// Retains press/release ordering when CoreBluetooth's transmit buffer is full.
struct HIDReportQueue {
    private(set) var pending: [HIDInputReport] = []
    let capacity: Int

    init(capacity: Int = 512) { self.capacity = capacity }

    mutating func append(_ report: HIDInputReport) throws {
        guard pending.count < capacity else { throw BLEHIDError.sendBackpressure }
        pending.append(report)
    }

    mutating func drain(transmit: (HIDInputReport) -> Bool) {
        while let next = pending.first {
            guard transmit(next) else { return }
            pending.removeFirst()
        }
    }

    mutating func removeAll() { pending.removeAll() }

    var isFull: Bool { pending.count >= capacity }
}
