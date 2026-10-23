import Foundation

public actor FrameStore {
    public static let maximumFrameBytes = OpenIPhoneUseLimits.maximumFrameBytes
    private var latest: ScreenshotFrame?
    // Keep only identifiers/times for guards, never historical JPEGs. At 60 fps this
    // covers the five-second control window without racing the next video frame.
    private var controlFrames: [(id: String, at: Date)] = []
    private var active = false
    private var lastError: String?
    private var pageReport: String?
    private var taps: [PointerTap] = []

    public init() {}

    /// A click the calibration board caught, as a screen fraction.
    public struct PointerTap: Sendable, Equatable {
        public let x: Double
        public let y: Double
        public let at: Date

        public init(x: Double, y: Double, at: Date) {
            self.x = x
            self.y = y
            self.at = at
        }
    }

    public func recordTap(x: Double, y: Double, at: Date = .now) {
        guard x >= 0, x <= 1, y >= 0, y <= 1 else { return }
        taps.append(PointerTap(x: x, y: y, at: at))
        if taps.count > 64 { taps.removeFirst(taps.count - 64) }
    }

    public func taps(since date: Date) -> [PointerTap] { taps.filter { $0.at >= date } }
    public func clearTaps() { taps.removeAll() }

    /// The capture page's own view of its video element, so a frozen element can be
    /// told apart from a stopped capture without guessing from pixels.
    public func setPageReport(_ value: String?) { pageReport = value }
    public func report() -> String? { pageReport }

    public func setCaptureActive(_ value: Bool, error: String? = nil) {
        active = value
        if !value { controlFrames.removeAll() }
        lastError = error
    }

    @discardableResult
    public func put(jpeg: Data, at date: Date = .now) throws -> ScreenshotFrame {
        guard jpeg.count <= Self.maximumFrameBytes else { throw FrameStoreError.tooLarge }
        guard jpeg.starts(with: [0xFF, 0xD8, 0xFF]) else { throw FrameStoreError.invalidJPEG }
        let frame = ScreenshotFrame(frameID: UUID().uuidString, capturedAt: date, jpeg: jpeg)
        latest = frame // Keep exactly one JPEG in memory.
        controlFrames.append((frame.frameID, frame.capturedAt))
        if controlFrames.count > 512 { controlFrames.removeFirst(controlFrames.count - 512) }
        active = true
        lastError = nil
        return frame
    }

    public func frameTime(for identifier: String) -> Date? {
        controlFrames.first(where: { $0.id == identifier })?.at
    }

    public func latestFrame() -> ScreenshotFrame? { latest }
    public func isCaptureActive() -> Bool { active }
    public func error() -> String? { lastError }

    public func validateControlFrame(expectedFrameID: String?, maximumAgeMilliseconds: Int = 5_000,
                                     now: Date = .now) throws {
        guard active else { throw ControlFrameError.captureInactive }
        guard let expectedFrameID, let latest else { throw ControlFrameError.missingFrame }
        guard let observed = controlFrames.first(where: { $0.id == expectedFrameID }),
              now.timeIntervalSince(observed.at) * 1_000 <= Double(maximumAgeMilliseconds),
              ageMilliseconds(of: latest, now: now) <= maximumAgeMilliseconds else {
            throw ControlFrameError.staleFrame
        }
    }

    public func ageMilliseconds(of frame: ScreenshotFrame, now: Date = .now) -> Int {
        max(0, Int(now.timeIntervalSince(frame.capturedAt) * 1_000))
    }
}

public enum FrameStoreError: Error, Equatable {
    case tooLarge
    case invalidJPEG
}

public enum ControlFrameError: Error, LocalizedError, Equatable {
    case captureInactive
    case missingFrame
    case staleFrame

    public var errorDescription: String? {
        switch self {
        case .captureInactive: "Screen capture is paused or stopped. Start the iPhone Open iPhone Use broadcast before sending input."
        case .missingFrame: "Read a current screenshot and pass its frame_id as expected_frame_id before sending input."
        case .staleFrame: "The screenshot is unknown, expired, or belongs to an earlier capture session. Read a new frame before sending input."
        }
    }
}

public enum OpenIPhoneUsePaths {
    public static var supportDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Open iPhone Use", isDirectory: true)
    }

    public static var socketPath: String {
        supportDirectory.appendingPathComponent("bridge.sock").path
    }
}
