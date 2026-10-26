// swift-tools-version: 6.0
import PackageDescription
import Foundation

let headlessInfoPath = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    .appendingPathComponent("Sources/OpenIPhoneUseHeadless/Info.plist").path

let package = Package(
    name: "OpenIPhoneUseMac",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "open-iphone-use-mcp", targets: ["OpenIPhoneUseMCP"]),
        .executable(name: "open-iphone-use-headless", targets: ["OpenIPhoneUseHeadless"]),
    ],
    dependencies: [
        .package(url: "https://github.com/modelcontextprotocol/swift-sdk.git", from: "0.11.0"),
    ],
    targets: [
        .target(
            name: "ObjCExceptionCatcher",
            path: "Sources/ObjCExceptionCatcher",
            publicHeadersPath: "include"
        ),
        .target(
            name: "OpenIPhoneUseCore",
            dependencies: ["ObjCExceptionCatcher"],
            resources: [.copy("Resources/capture.html")]
        ),
        .executableTarget(
            name: "OpenIPhoneUseMCP",
            dependencies: ["OpenIPhoneUseCore", .product(name: "MCP", package: "swift-sdk")]
        ),
        .executableTarget(
            name: "OpenIPhoneUseHeadless", dependencies: ["OpenIPhoneUseCore", "ObjCExceptionCatcher"],
            exclude: ["Info.plist"],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                                          "-Xlinker", "__info_plist", "-Xlinker", headlessInfoPath])]
        ),
        .testTarget(name: "OpenIPhoneUseCoreTests", dependencies: ["OpenIPhoneUseCore"]),
    ]
)
