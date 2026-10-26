import XCTest
@testable import OpenIPhoneUseCore

final class BluetoothPeerNameTests: XCTestCase {
    func testGenericModelNamesDoNotPreventLookingForTheOwnersName() {
        XCTAssertNil(BluetoothPeerName.specific("iPhone"))
        XCTAssertNil(BluetoothPeerName.specific("  IPHONE  "))
        XCTAssertEqual(BluetoothPeerName.specific("Sems's iPhone"), "Sems's iPhone")
        XCTAssertEqual(BluetoothPeerName.specific("Sems\n iPhone"), "Sems iPhone")
    }
}
