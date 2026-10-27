import XCTest
@testable import OpenIPhoneUseCore

final class PointerAimTests: XCTestCase {
    @MainActor func testCorrectsObservedMissBeforeAllowingClick() async throws {
        var points = [PointerLocator.Point(x: 0.259, y: 0.647), .init(x: 0.25, y: 0.7)]
        var moves: [(Int, Int)] = []
        try await PointerAim.align(target: .init(x: 0.25, y: 0.7), span: .init(width: 600, height: 1000),
                                   observe: { points.removeFirst() }, move: { moves.append(($0, $1)) })
        XCTAssertEqual(moves.count, 1)
        XCTAssertEqual(moves[0].0, -5)
        XCTAssertEqual(moves[0].1, 53)
    }

    @MainActor func testUnobservableAndNonconvergentTargetsFailWithoutClick() async throws {
        var moves = 0
        do {
            try await PointerAim.align(target: .init(x: 0.4, y: 0.4), span: .init(width: 600, height: 1000),
                                       observe: { .init(x: 0.3, y: 0.3) }, move: { _, _ in moves += 1 })
            XCTFail("must not permit a click")
        } catch PointerAim.Failure.didNotConverge { XCTAssertEqual(moves, 3) }
        moves = 0
        do {
            try await PointerAim.align(target: .init(x: 0.4, y: 0.4), span: .init(width: 600, height: 1000),
                                       observe: { nil }, move: { _, _ in moves += 1 })
            XCTFail("must not permit a click")
        } catch PointerAim.Failure.unavailable { XCTAssertEqual(moves, 0) }
    }
}
