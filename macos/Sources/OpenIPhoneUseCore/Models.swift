import Foundation

public struct DeviceSummary: Codable, Sendable, Equatable {
    public let deviceID: String
    public let name: String
    public let transport: String
    public let status: String
}

public struct DeviceStatus: Codable, Sendable, Equatable {
    public let deviceID: String
    public let name: String
    public let bluetoothState: String
    public let advertising: Bool
    public let connected: Bool
    public let centralIdentifier: String?
    public let captureActive: Bool
    public let lastFrameID: String?
    public let lastFrameAt: Date?
    public let frameAgeMilliseconds: Int?
    public let lastCommandID: String?
    public let lastError: String?
    public let sentReports: Int?
    public let rejectedReports: Int?
    public let pageReport: String?
    public let calibrationReady: Bool?
    public let calibrationValidationMiss: Double?
    public var calibrationEvidence: String? = nil
    public var calibrationDiagnostics: [String]? = nil
    public var pointerMotionProfile: String? = nil
    public var queuedReports: Int? = nil
}

public struct ScreenshotFrame: Sendable {
    public let frameID: String
    public let capturedAt: Date
    public let jpeg: Data
}

public struct BrokerRequest: Codable, Sendable {
    public var requestID: String = UUID().uuidString
    public let operation: String
    public var deviceID: String?
    public var expectedFrameID: String?
    public var dx: Int?
    public var dy: Int?
    public var button: String?
    public var text: String?
    public var key: String?
    public var steps: Int?
    public var intervalMilliseconds: Int?
    public var x: Double?
    public var y: Double?
    public var aim: Bool?
    public var profile: String?
    public var previousCommandID: String?
    public var observedX: Double?
    public var observedY: Double?

    public init(operation: String, deviceID: String? = nil, expectedFrameID: String? = nil,
                dx: Int? = nil, dy: Int? = nil, button: String? = nil,
                text: String? = nil, key: String? = nil,
                steps: Int? = nil, intervalMilliseconds: Int? = nil,
                x: Double? = nil, y: Double? = nil, aim: Bool? = nil,
                profile: String? = nil, previousCommandID: String? = nil,
                observedX: Double? = nil, observedY: Double? = nil) {
        self.operation = operation
        self.deviceID = deviceID
        self.expectedFrameID = expectedFrameID
        self.dx = dx
        self.dy = dy
        self.button = button
        self.text = text
        self.key = key
        self.steps = steps
        self.intervalMilliseconds = intervalMilliseconds
        self.x = x
        self.y = y
        self.aim = aim
        self.profile = profile
        self.previousCommandID = previousCommandID
        self.observedX = observedX
        self.observedY = observedY
    }
}

public struct BrokerResponse: Codable, Sendable {
    public let requestID: String
    public let ok: Bool
    public let message: String
    public let commandID: String?
    public let devices: [DeviceSummary]?
    public let status: DeviceStatus?
    public let frame: ScreenshotFrameWire?

    public init(requestID: String, ok: Bool, message: String, commandID: String? = nil,
                devices: [DeviceSummary]? = nil, status: DeviceStatus? = nil,
                frame: ScreenshotFrameWire? = nil) {
        self.requestID = requestID
        self.ok = ok
        self.message = message
        self.commandID = commandID
        self.devices = devices
        self.status = status
        self.frame = frame
    }
}

public struct ScreenshotFrameWire: Codable, Sendable {
    public let frameID: String
    public let capturedAt: Date
    public let ageMilliseconds: Int
    public let captureActive: Bool
    public let mimeType: String
    public let base64: String
}

public enum OpenIPhoneUseLimits {
    public static let maximumFrameBytes = 4 * 1024 * 1024
    public static let maximumBrokerMessageBytes = 8 * 1024 * 1024
}
