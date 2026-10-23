// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "OpenIPhoneUseMac",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "OpenIPhoneUseMac", targets: ["OpenIPhoneUseMac"]),
        .executable(name: "open-iphone-use-mcp", targets: ["OpenIPhoneUseMCP"]),
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
            name: "OpenIPhoneUseMac",
            dependencies: ["OpenIPhoneUseCore"],
            exclude: ["Info.plist"]
        ),
        .executableTarget(
            name: "OpenIPhoneUseMCP",
            dependencies: ["OpenIPhoneUseCore", .product(name: "MCP", package: "swift-sdk")]
        ),
        .testTarget(name: "OpenIPhoneUseCoreTests", dependencies: ["OpenIPhoneUseCore"]),
    ]
)
