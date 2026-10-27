import XCTest
@testable import OpenIPhoneUseCore

final class PerCentralReportQueueTests: XCTestCase {
    @MainActor
    func testBlockedCentralDoesNotBlockAnotherAndReadyResumesOnlyItsOwnFIFO() async throws {
        var blocked = true
        var sent: [(String, Int, Data)] = []
        let queue = PerCentralReportQueue { peer, report, data in
            if peer == "a" && blocked { return false }
            sent.append((peer, report, data)); return true
        }
        let a = Task { try await queue.send(peer: "a", report: 2, payload: Data([1, 4, 0, 0])) }
        await Task.yield()
        try await queue.send(peer: "b", report: 1, payload: Data([2, 0, 4, 0, 0, 0, 0, 0]))
        XCTAssertEqual(sent.map(\.0), ["b"])
        XCTAssertEqual(queue.value(peer: "a", report: 2), Data(repeating: 0, count: 4))
        blocked = false; queue.ready()
        try await a.value
        XCTAssertEqual(sent.map(\.0), ["b", "a"])
        XCTAssertEqual(queue.value(peer: "a", report: 2), Data([1, 4, 0, 0]))
        XCTAssertEqual(queue.value(peer: "b", report: 2), Data(repeating: 0, count: 4))
        queue.cancel(peer: "a", forget: true)
        XCTAssertEqual(queue.value(peer: "a", report: 2), Data(repeating: 0, count: 4))
        XCTAssertEqual(queue.value(peer: "b", report: 1), Data([2, 0, 4, 0, 0, 0, 0, 0]))
    }
    @MainActor
    func testPeerCancellationDoesNotCancelOtherPeerAndTimeoutIsBounded() async throws {
        let queue = PerCentralReportQueue(timeout: .milliseconds(50)) { _, _, _ in false }
        let a = Task { try await queue.send(peer: "a", report: 2, payload: Data(repeating: 0, count: 4)) }
        let b = Task { try await queue.send(peer: "b", report: 2, payload: Data(repeating: 0, count: 4)) }
        await Task.yield(); queue.cancel(peer: "a")
        do { try await a.value; XCTFail("Expected peer cancellation") } catch is CancellationError {} catch { XCTFail("Wrong error") }
        do { try await b.value; XCTFail("Expected peer timeout") } catch {
            XCTAssertTrue(error.localizedDescription.contains("backpressure timeout"))
        }
    }
    @MainActor
    func testQueuedPressReleaseOrderIsPreservedPerCentral() async throws {
        var blocked = true
        var values: [UInt8] = []
        let queue = PerCentralReportQueue { _, _, data in
            if blocked { return false }; values.append(data[0]); return true
        }
        let press = Task { try await queue.send(peer: "a", report: 2, payload: Data([1, 0, 0, 0])) }
        await Task.yield()
        let release = Task { try await queue.send(peer: "a", report: 2, payload: Data([0, 0, 0, 0])) }
        await Task.yield(); blocked = false; queue.ready()
        try await press.value; try await release.value
        XCTAssertEqual(values, [1, 0])
    }
    @MainActor
    func testGlobalReadyCapacityIsSharedFairlyAcrossCentrals() async throws {
        var capacity = 0
        var sent: [String] = []
        let queue = PerCentralReportQueue { peer, _, _ in
            guard capacity > 0 else { return false }
            capacity -= 1; sent.append(peer); return true
        }
        let a1 = Task { try await queue.send(peer: "a", report: 2, payload: Data([1, 0, 0, 0])) }
        await Task.yield()
        let a2 = Task { try await queue.send(peer: "a", report: 2, payload: Data([0, 0, 0, 0])) }
        let b = Task { try await queue.send(peer: "b", report: 2, payload: Data([0, 1, 0, 0])) }
        await Task.yield()
        capacity = 1; queue.ready()
        capacity = 1; queue.ready()
        XCTAssertEqual(sent, ["a", "b"])
        capacity = 1; queue.ready()
        try await a1.value; try await a2.value; try await b.value
    }

}
