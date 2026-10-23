import XCTest
@testable import OpenIPhoneUseCore

final class PointerRetryTests: XCTestCase {
    func testCorrectionUsesObservedMissNotRandomJitter() throws {
        let delta = try XCTUnwrap(PointerRetry.correction(targetX: 0.5, targetY: 0.8,
                                                         observedX: 0.47, observedY: 0.82))
        XCTAssertEqual(delta.x, 0.03, accuracy: 0.0001)
        XCTAssertEqual(delta.y, -0.02, accuracy: 0.0001)
    }
    func testRejectsUnsafeOrAbsentMiss() {
        for observed in [(0.1, 0.8), (0.5, 0.8), (Double.nan, 0.8), (0.5, 1.1)] {
            XCTAssertNil(PointerRetry.correction(targetX: 0.5, targetY: 0.8,
                                                 observedX: observed.0, observedY: observed.1))
        }
    }
}
