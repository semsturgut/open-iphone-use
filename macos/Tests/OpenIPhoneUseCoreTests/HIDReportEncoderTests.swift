import XCTest
@testable import OpenIPhoneUseCore

final class HIDReportEncoderTests: XCTestCase {
    func testGATTKeyboardPayloadHasEightBytesWithoutReportID() {
        XCTAssertEqual(Array(HIDReportEncoder.keyboard(modifier: 2, usages: [4, 5])), [2, 0, 4, 5, 0, 0, 0, 0])
        XCTAssertEqual(Array(HIDReportEncoder.keyboard(modifier: 0, usages: [])), Array(repeating: 0, count: 8))
    }

    func testTextMappingAndShift() throws {
        XCTAssertEqual(try HIDReportEncoder.text("aZ!").map(\.usage), [0x04, 0x1D, 0x1E])
        XCTAssertEqual(try HIDReportEncoder.text("aZ!").map(\.modifier), [0, 2, 2])
    }

    func testUnsupportedTextFailsBeforeTyping() {
        XCTAssertThrowsError(try HIDReportEncoder.text("ğ"))
        XCTAssertThrowsError(try HIDReportEncoder.text(String(repeating: "a", count: 501)))
    }

    func testMouseBoundsAndButtons() throws {
        XCTAssertEqual(try HIDReportEncoder.buttonMask("right"), 2)
        XCTAssertThrowsError(try HIDReportEncoder.buttonMask("side"))
        XCTAssertThrowsError(try HIDReportEncoder.mouse(dx: 128))
    }

    func testGATTMouseAxesRemainInSeparateSignedFields() throws {
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(dx: 24)), [0, 24, 0, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(dx: -24)), [0, 232, 0, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(dy: 24)), [0, 0, 24, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(dy: -24)), [0, 0, 232, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(wheel: -1)), [0, 0, 0, 255])
    }

    func testPositiveFeedAndScrollShareTheNativeHIDWheelSign() throws {
        let next = try XCTUnwrap(FeedDirection.wheelDelta(items: 1, amount: 120))
        let previous = try XCTUnwrap(FeedDirection.wheelDelta(items: -1, amount: 120))
        XCTAssertEqual(next, 120)
        XCTAssertEqual(previous, -120)
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(wheel: next)), [0, 0, 0, 120])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(wheel: previous)), [0, 0, 0, 136])
        XCTAssertNil(FeedDirection.wheelDelta(items: 2, amount: 120))
    }

    func testGATTMouseClicksDoNotMoveEitherAxisOrLeaveButtonsPressed() throws {
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(buttons: HIDReportEncoder.buttonMask("left"))), [1, 0, 0, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse(buttons: HIDReportEncoder.buttonMask("right"))), [2, 0, 0, 0])
        XCTAssertEqual(Array(try HIDReportEncoder.mouse()), [0, 0, 0, 0])
    }

    func testBackpressurePreservesReportRoutingAndPressReleaseOrder() throws {
        let press = HIDInputReport(reportID: 2, payload: Data([1, 0, 0, 0]))
        let release = HIDInputReport(reportID: 2, payload: Data([0, 0, 0, 0]))
        let keyboard = HIDInputReport(reportID: 1, payload: Data(repeating: 0, count: 8))
        var queue = HIDReportQueue(capacity: 3)
        try queue.append(press)
        queue.drain { _ in false }
        try queue.append(release)
        try queue.append(keyboard)
        XCTAssertThrowsError(try queue.append(press))
        XCTAssertEqual(queue.pending, [press, release, keyboard])
        var sent: [HIDInputReport] = []
        queue.drain { report in
            guard sent.count < 1 else { return false }
            sent.append(report)
            return true
        }
        XCTAssertEqual(sent, [press])
        XCTAssertEqual(queue.pending, [release, keyboard])
        queue.drain { sent.append($0); return true }
        XCTAssertEqual(sent, [press, release, keyboard])
        XCTAssertTrue(queue.pending.isEmpty)
    }
}
