import Foundation
import XCTest
@testable import OpenIPhoneUseCore

final class UnixSocketBrokerTests: XCTestCase {
    func testSameUserRequestRoundTripsThroughPrivateSocket() throws {
        let socket = "/tmp/open-iphone-use-test-\(UUID().uuidString).sock"
        let broker = UnixSocketBroker(path: socket) { request in
            BrokerResponse(requestID: request.requestID, ok: true, message: request.operation,
                           commandID: "command-1")
        }
        try broker.start()
        defer { broker.stop() }

        let response = try UnixSocketBrokerClient.request(BrokerRequest(operation: "test"), socketPath: socket)
        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.message, "test")
        XCTAssertEqual(response.commandID, "command-1")
    }
}
