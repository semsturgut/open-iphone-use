import XCTest
@testable import OpenIPhoneUseCore

final class PointerMotionTests: XCTestCase {
    func testPlanSumsExactlyToRequestedDelta() {
        for (dx, dy) in [(24, 0), (0, -24), (100, 100), (-127, 127), (127, 0), (1, 1), (7, -3)] {
            let plan = PointerMotion.plan(dx: dx, dy: dy)
            let sumX = plan.deltas.reduce(0) { $0 + $1.dx }
            let sumY = plan.deltas.reduce(0) { $0 + $1.dy }
            XCTAssertEqual(sumX, dx, "dx \(dx) dy \(dy)")
            XCTAssertEqual(sumY, dy, "dx \(dx) dy \(dy)")
        }
    }

    func testEveryStepStaysInsideHIDReportRange() {
        for (dx, dy) in [(127, 127), (-127, 127), (127, -127), (100, 0)] {
            let plan = PointerMotion.plan(dx: dx, dy: dy)
            for delta in plan.deltas {
                XCTAssertLessThanOrEqual(abs(delta.dx), PointerMotion.maximumDelta)
                XCTAssertLessThanOrEqual(abs(delta.dy), PointerMotion.maximumDelta)
            }
        }
    }

    func testStepsAreManyAndProgressivelySized() {
        let plan = PointerMotion.plan(dx: 100, dy: 0)
        XCTAssertGreaterThanOrEqual(plan.deltas.count, 10, "a 100 unit move must not be one jump")
        let first = plan.deltas.first!.dx
        let middle = plan.deltas[plan.deltas.count / 2].dx
        XCTAssertLessThan(first, middle, "smoothstep must ease in: first step smaller than the middle")
        XCTAssertGreaterThan(first, 0)
    }

    func testDistanceScalesStepCountWithinBounds() {
        XCTAssertEqual(PointerMotion.stepCount(forDistance: 0), 0)
        XCTAssertEqual(PointerMotion.stepCount(forDistance: 1), 4)
        XCTAssertEqual(PointerMotion.stepCount(forDistance: 60), 15)
        XCTAssertEqual(PointerMotion.stepCount(forDistance: 10_000), 48)
    }

    func testIntervalScalesWithDistanceAndStaysClamped() {
        let near = PointerMotion.plan(dx: 10, dy: 0)
        let far = PointerMotion.plan(dx: 127, dy: 127)
        XCTAssertGreaterThan(near.intervalMilliseconds, far.intervalMilliseconds)
        XCTAssertGreaterThanOrEqual(far.intervalMilliseconds, PointerMotion.minimumIntervalMilliseconds)
        XCTAssertLessThanOrEqual(near.intervalMilliseconds, PointerMotion.maximumIntervalMilliseconds)
    }

    func testExplicitStepAndIntervalOverridesAreHonoured() {
        let plan = PointerMotion.plan(dx: 40, dy: 0, steps: 8, intervalMilliseconds: 6)
        XCTAssertLessThanOrEqual(plan.deltas.count, 8)
        XCTAssertGreaterThanOrEqual(plan.deltas.count, 6)
        XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dx }, 40)
        XCTAssertEqual(plan.intervalMilliseconds, PointerMotion.minimumIntervalMilliseconds)
    }

    func testZeroLengthStepsAreDropped() {
        let plan = PointerMotion.plan(dx: 3, dy: 0, steps: 20)
        XCTAssertFalse(plan.deltas.contains { $0.dx == 0 && $0.dy == 0 })
        XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dx }, 3)
    }

    func testEveryCalibratedProfilePreservesDistanceAndReportBounds() {
        for profile in PointerMotion.Profile.allCases {
            for (dx, dy) in [(150, 150), (350, 350), (600, 1400), (-15, 21)] {
                let plan = PointerMotion.calibratedPlan(dx: dx, dy: dy, profile: profile)
                XCTAssertEqual(plan.intervalMilliseconds, profile.intervalMilliseconds)
                XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dx }, dx)
                XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dy }, dy)
                var horizontalStarted = false
                for delta in plan.deltas {
                    XCTAssertLessThanOrEqual(abs(delta.dx), profile.reportUnits)
                    XCTAssertLessThanOrEqual(abs(delta.dy), profile.reportUnits)
                    XCTAssertTrue(delta.dx == 0 || delta.dy == 0)
                    if delta.dx != 0 { horizontalStarted = true }
                    if horizontalStarted { XCTAssertEqual(delta.dy, 0) }
                }
            }
        }
    }

    func testTurboUsesEightTimesFewerReportsWithoutArtificialDelay() {
        let slow = PointerMotion.calibratedPlan(dx: 800, dy: 1400, profile: .precise4)
        let fast = PointerMotion.calibratedPlan(dx: 800, dy: 1400, profile: .turbo32)
        XCTAssertEqual(fast.intervalMilliseconds, 8)
        XCTAssertLessThanOrEqual(fast.deltas.count, (slow.deltas.count + 7) / 8 + 1)
        XCTAssertEqual(PointerMotion.Profile.allCases.first, .burst127)
        let burst = PointerMotion.calibratedPlan(dx: 800, dy: 1400, profile: .burst127)
        XCTAssertEqual(burst.deltas.count, 19)
        XCTAssertEqual(burst.intervalMilliseconds, 8)
    }

    func testZeroMovementProducesNoReports() {
        XCTAssertTrue(PointerMotion.plan(dx: 0, dy: 0).deltas.isEmpty)
    }

    func testOversizedRequestIsClampedNotRejected() {
        let plan = PointerMotion.plan(dx: 400, dy: -400)
        XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dx }, PointerMotion.maximumDelta)
        XCTAssertEqual(plan.deltas.reduce(0) { $0 + $1.dy }, -PointerMotion.maximumDelta)
    }
}
