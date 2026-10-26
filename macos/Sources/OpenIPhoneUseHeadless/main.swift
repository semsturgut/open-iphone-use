import Foundation
import Darwin
import OpenIPhoneUseCore

// No SwiftUI/AppKit lifecycle or capture listener. Python owns HTTP/TLS and credentials.
@main
struct HeadlessBridge {
    @MainActor static func main() async throws {
        guard ProcessInfo.processInfo.environment["OIU_HEADLESS"] == "1",
              ProcessInfo.processInfo.environment["OIU_SUPPORT_DIRECTORY"] != nil else {
            fputs("Launch through the portable server; dedicated support directory required.\n", stderr)
            return
        }
        let controller = OpenIPhoneUseController(headless: true)
        // Keep CoreBluetooth and the serialized controller alive without a GUI app.
        let parent = getppid()
        while !Task.isCancelled {
            try await Task.sleep(for: .seconds(1))
            if getppid() != parent {
                _ = await controller.handle(BrokerRequest(operation: "stop_control", deviceID: "iphone"))
                controller.stopHID()
                return
            }
        }
    }
}
