import Foundation
import XCTest
@testable import OpenIPhoneUseCore

final class MCPHandshakeCompatibilityTests: XCTestCase {
    func testObjectCapabilitiesDoNotBreakInitializationAndStandardFieldsSurvive() throws {
        let data = Data(#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"codex-mcp-client","version":"1"},"capabilities":{"elicitation":{"form":{},"url":{}},"experimental":{"ui":{"mimeTypes":["text/html"]},"flag":true,"legacy":"yes"}}}}"#.utf8)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: MCPHandshakeCompatibility.normalize(data)) as? [String: Any])
        let params = try XCTUnwrap(object["params"] as? [String: Any])
        let caps = try XCTUnwrap(params["capabilities"] as? [String: Any])
        XCTAssertEqual(caps["experimental"] as? [String: String], ["legacy": "yes"])
        XCTAssertNotNil(caps["elicitation"])
        XCTAssertEqual(params["protocolVersion"] as? String, "2025-06-18")
        XCTAssertEqual(object["id"] as? Int, 0)
    }

    func testToolArgumentsAreNeverRewritten() {
        let input = Data(#"{"method":"tools/call","params":{"capabilities":{"experimental":{"value":{}}}}}"#.utf8)
        XCTAssertEqual(MCPHandshakeCompatibility.normalize(input), input)
    }

    func testNormalAndMalformedMessagesPassThrough() {
        for text in [#"{"method":"initialize","params":{"capabilities":{}}}"#,
                     #"{"method":"initialize","params":{"capabilities":{"experimental":{"legacy":"yes"}}}}"#,
                     "invalid"] {
            let data = Data(text.utf8)
            XCTAssertEqual(MCPHandshakeCompatibility.normalize(data), data)
        }
    }
}
