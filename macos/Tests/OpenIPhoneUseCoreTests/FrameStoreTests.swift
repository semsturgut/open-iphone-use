import XCTest
@testable import OpenIPhoneUseCore

final class FrameStoreTests: XCTestCase {
    func testRetainsOnlyNewestJpegInMemory() async throws {
        let store = FrameStore()
        let first = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF, 0x01]), at: Date(timeIntervalSince1970: 100))
        let second = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF, 0x02]), at: Date(timeIntervalSince1970: 101))
        let latest = await store.latestFrame()
        XCTAssertNotEqual(first.frameID, second.frameID)
        XCTAssertEqual(latest?.frameID, second.frameID)
        XCTAssertEqual(latest?.jpeg, Data([0xFF, 0xD8, 0xFF, 0x02]))
        let active = await store.isCaptureActive()
        XCTAssertTrue(active)
    }

    func testRejectsNonJpegAndOversizedFrames() async {
        let store = FrameStore()
        do {
            _ = try await store.put(jpeg: Data([0, 1, 2]))
            XCTFail("Non-JPEG payload should be rejected")
        } catch { XCTAssertEqual(error as? FrameStoreError, .invalidJPEG) }
        do {
            _ = try await store.put(jpeg: Data(repeating: 0, count: OpenIPhoneUseLimits.maximumFrameBytes + 1))
            XCTFail("Oversized payload should be rejected")
        } catch { XCTAssertEqual(error as? FrameStoreError, .tooLarge) }
    }

    func testFrameAgeIsReportedInMilliseconds() async throws {
        let store = FrameStore()
        let instant = Date(timeIntervalSince1970: 100)
        let frame = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF]), at: instant)
        let age = await store.ageMilliseconds(of: frame, now: instant.addingTimeInterval(2.5))
        XCTAssertEqual(age, 2_500)
    }

    func testPausedCaptureRejectsEvenAFreshUnchangedFrame() async throws {
        let store = FrameStore()
        let now = Date(timeIntervalSince1970: 100)
        let frame = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF]), at: now)
        try await store.validateControlFrame(expectedFrameID: frame.frameID, now: now)
        await store.setCaptureActive(false, error: "Safari entered the background")
        do {
            try await store.validateControlFrame(expectedFrameID: frame.frameID, now: now.addingTimeInterval(0.1))
            XCTFail("A paused capture must reject input even when the old frame is fresh")
        } catch { XCTAssertEqual(error as? ControlFrameError, .captureInactive) }
        let retained = await store.latestFrame()
        XCTAssertEqual(retained?.frameID, frame.frameID)
        let resumed = try await store.put(jpeg: frame.jpeg, at: now.addingTimeInterval(1))
        try await store.validateControlFrame(expectedFrameID: resumed.frameID, now: now.addingTimeInterval(1))
        let message = await store.error()
        XCTAssertNil(message)
    }

    func testExpiredOrUnknownFramesCannotAuthorizeInput() async throws {
        let store = FrameStore()
        let now = Date(timeIntervalSince1970: 100)
        let old = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF]), at: now)
        do {
            try await store.validateControlFrame(expectedFrameID: old.frameID, now: now.addingTimeInterval(6))
            XCTFail("An expired frame must reject input")
        } catch { XCTAssertEqual(error as? ControlFrameError, .staleFrame) }
        _ = try await store.put(jpeg: old.jpeg, at: now.addingTimeInterval(1))
        try await store.validateControlFrame(expectedFrameID: old.frameID, now: now.addingTimeInterval(1))
        do {
            try await store.validateControlFrame(expectedFrameID: "invented", now: now.addingTimeInterval(1))
            XCTFail("An unknown frame cannot authorize input")
        } catch { XCTAssertEqual(error as? ControlFrameError, .staleFrame) }
        _ = try await store.put(jpeg: old.jpeg, at: now.addingTimeInterval(6))
        do {
            try await store.validateControlFrame(expectedFrameID: old.frameID, now: now.addingTimeInterval(6))
            XCTFail("A live stream must not extend an observed frame's expiry")
        } catch { XCTAssertEqual(error as? ControlFrameError, .staleFrame) }
    }
    func testVideoUpdatesDoNotInvalidateAnObservedFrameButRestartDoes() async throws {
        let store = FrameStore()
        let now = Date(timeIntervalSince1970: 100)
        let first = try await store.put(jpeg: Data([0xFF, 0xD8, 0xFF]), at: now)
        for frame in 1...240 {
            _ = try await store.put(jpeg: first.jpeg, at: now.addingTimeInterval(Double(frame) / 60))
        }
        try await store.validateControlFrame(expectedFrameID: first.frameID, now: now.addingTimeInterval(4))
        await store.setCaptureActive(false)
        let resumed = try await store.put(jpeg: first.jpeg, at: now.addingTimeInterval(4.1))
        do {
            try await store.validateControlFrame(expectedFrameID: first.frameID, now: now.addingTimeInterval(4.1))
            XCTFail("Capture restart must invalidate prior authorization")
        } catch { XCTAssertEqual(error as? ControlFrameError, .staleFrame) }
        try await store.validateControlFrame(expectedFrameID: resumed.frameID, now: now.addingTimeInterval(4.1))
    }

}
