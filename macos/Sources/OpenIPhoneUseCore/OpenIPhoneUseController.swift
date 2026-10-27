import Combine
import Foundation

@MainActor
public final class OpenIPhoneUseController: ObservableObject {
    public static let deviceID = BLEHIDPeripheral.deviceID
    public let frames = FrameStore()
    public let hid = BLEHIDPeripheral()
    private let receiver: LocalCaptureReceiver

    @Published public private(set) var bluetoothState = "notStarted"
    @Published public private(set) var isAdvertising = false
    @Published public private(set) var isConnected = false
    @Published public private(set) var centralIdentifier: String?
    @Published public private(set) var lastError: String?
    @Published public private(set) var lastCommandID: String?
    @Published public private(set) var receiverState = "notStarted"
    @Published public private(set) var receiverAddress: String?
    @Published public private(set) var rootCertificatePath: String?
    @Published public private(set) var latestFrame: ScreenshotFrame?
    @Published public private(set) var latestFrameAgeMilliseconds: Int?
    @Published public private(set) var isCaptureLive = false
    @Published public internal(set) var pointerCalibration = PointerCalibration()
    @Published public internal(set) var pointerNote = "Pointer not calibrated."
    /// Requested Safari upload rate, independent of the MCP agent’s inference frequency.
    @Published public internal(set) var captureFramesPerSecond = 30
    public static let captureRateOptions = [1, 5, 10, 15, 30, 60]
    static let captureRateKey = "OpenIPhoneUseCaptureFramesPerSecond"

    /// Where the pointer is in mouse units from home; nil means unknown.
    var pointerUnits: (x: Int, y: Int)?
    var calibrationDiagnostics: [String] = []
    var pointerMotionProfile: PointerMotion.Profile = .turbo32
    struct PointerAttempt {
        let commandID: String
        let x: Double, y: Double
        let unitsX: Int, unitsY: Int
        let at: Date
    }
    var lastPointerAttempt: PointerAttempt?
    static var pointerCalibrationKey: String { "OpenIPhoneUsePointerCalibration" + (ProcessInfo.processInfo.environment["OIU_DEVICE_NAMESPACE"] ?? "") }
    /// Mouse units the pointer is wiggled by when it has to be found on screen.
    static let pointerWiggleUnits = 12
    /// A miss bigger than this is corrected before pressing.
    static let pointerAimTolerance = 0.008
    /// Stops used for the automatic (wiggle) calibration, in units from home.
    static let pointerCalibrationStops = [150, 350]

    private var lastBluetoothError: String?
    private var broker: UnixSocketBroker?
    private var captureWatchdog: Task<Void, Never>?
    private var queuedActions: [(BrokerRequest, CheckedContinuation<BrokerResponse, Never>)] = []
    private var processingAction = false
    private var activeActionTask: Task<Void, Never>?
    private let maximumFrameAgeMilliseconds = 5_000

    public init(headless: Bool = false) {
        receiver = LocalCaptureReceiver(frames: frames)
        receiver.onCalibrationRequest = { [weak self] force in
            guard let self else { return nil }
            if force {
                return await self.handle(BrokerRequest(operation: "prepare_session", deviceID: Self.deviceID))
            }
            // Sharing itself does not authorize surprise input. The first agent request
            // (ensure_session) prepares calibration; a share-start check is read-only.
            let status = await self.deviceStatus()
            return BrokerResponse(requestID: UUID().uuidString, ok: status.calibrationReady == true,
                                  message: status.calibrationReady == true ? "Calibration ready."
                                    : "Screen sharing active. The agent checks calibration when you start a task.")
        }
        hid.onChange = { [weak self] in self?.refreshBluetoothState() }
        let localBroker = UnixSocketBroker { [weak self] request in
            guard let self else {
                return BrokerResponse(requestID: request.requestID, ok: false, message: "Open iPhone Use app is shutting down.")
            }
            return await self.handle(request)
        }
        broker = localBroker
        do { try localBroker.start() }
        catch { lastError = error.localizedDescription }
        loadPointerCalibration()
        let savedRate = UserDefaults.standard.integer(forKey: Self.captureRateKey)
        captureFramesPerSecond = Self.captureRateOptions.contains(savedRate) ? savedRate : 30
        receiver.framesPerSecond = captureFramesPerSecond
        captureWatchdog = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                if let self { await self.refreshCaptureState() } else { return }
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            }
        }
        // Start the radio and the receiver on launch. Both are what the phone connects to, and
        // an agent cannot press a button in the app; requiring it made every restart a manual
        // step and left the phone reporting "capture paused" until someone noticed.
        if !headless || ProcessInfo.processInfo.environment["OIU_DISABLE_BLUETOOTH"] != "1" { startHID() }
        if !headless { startCaptureReceiver() }
    }

    deinit { captureWatchdog?.cancel() }

    /// Asks the capture page to show or hide the calibration board.
    public func setPageBoard(open: Bool) {
        receiver.boardOpen = open
        receiver.boardEpoch += 1
    }

    public func setCaptureFramesPerSecond(_ value: Int) {
        guard Self.captureRateOptions.contains(value) else { return }
        captureFramesPerSecond = value
        receiver.framesPerSecond = value
        UserDefaults.standard.set(value, forKey: Self.captureRateKey)
    }

    public func startHID() {
        hid.startAdvertising()
        refreshBluetoothState()
    }

    public func stopHID() {
        hid.stopAdvertising()
        refreshBluetoothState()
    }

    public func startCaptureReceiver() {
        do {
            try receiver.start { [weak self] state, address, error in
                self?.receiverState = state
                self?.receiverAddress = address
                if let error { self?.lastError = error }
            }
            rootCertificatePath = LocalCertificateManager.rootCertificateURL.path
            lastError = nil
        } catch {
            receiverState = "error"
            lastError = error.localizedDescription
        }
    }

    public func stopCaptureReceiver() {
        receiver.stop()
        receiverState = "stopped"
        receiverAddress = nil
        Task { await frames.setCaptureActive(false) }
    }

    public func refreshBluetoothState() {
        bluetoothState = hid.state
        isAdvertising = hid.isAdvertising
        isConnected = hid.isConnected
        centralIdentifier = hid.centralIdentifier
        if lastError == lastBluetoothError || hid.lastError != nil { lastError = hid.lastError }
        lastBluetoothError = hid.lastError
    }

    public func deviceStatus() async -> DeviceStatus {
        let latest = await frames.latestFrame()
        let wasActive = await frames.isCaptureActive()
        let frameError = await frames.error()
        let frameAge: Int?
        if let latest { frameAge = await frames.ageMilliseconds(of: latest) }
        else { frameAge = nil }
        let active = wasActive && (frameAge.map { $0 <= maximumFrameAgeMilliseconds } ?? false)
        return DeviceStatus(
            deviceID: Self.deviceID,
            name: "iPhone (BLE HID)",
            bluetoothState: bluetoothState,
            advertising: isAdvertising,
            connected: isConnected,
            centralIdentifier: centralIdentifier,
            captureActive: active,
            lastFrameID: latest?.frameID,
            lastFrameAt: latest?.capturedAt,
            frameAgeMilliseconds: frameAge,
            lastCommandID: lastCommandID,
            lastError: lastError ?? frameError,
            sentReports: hid.sentReportCount,
            rejectedReports: hid.rejectedReportCount,
            pageReport: await frames.report(),
            calibrationReady: isConnected && active && pointerCalibration.canReuse(
                forScreenAspect: latest.flatMap { frameAspect($0) }, deviceIdentifier: centralIdentifier),
            calibrationValidationMiss: pointerCalibration.quality,
            calibrationEvidence: pointerCalibration.evidence?.rawValue,
            calibrationDiagnostics: calibrationDiagnostics,
            pointerMotionProfile: pointerMotionProfile.rawValue,
            queuedReports: hid.pendingReportCount
        )
    }

    public func refreshCaptureState() async {
        let latest = await frames.latestFrame()
        let age: Int?
        if let latest {
            age = await frames.ageMilliseconds(of: latest)
        } else {
            age = nil
        }
        let receiving = await frames.isCaptureActive()
        let active = receiving && (age.map { $0 <= maximumFrameAgeMilliseconds } ?? false)
        if isCaptureLive && !active { hid.releaseAll() }

        if latest?.frameID != latestFrame?.frameID {
            latestFrame = latest
        }
        latestFrameAgeMilliseconds = age
        isCaptureLive = active
    }

    public func handle(_ request: BrokerRequest) async -> BrokerResponse {
        guard request.operation == "list_devices" || request.operation == "guide" || request.deviceID == Self.deviceID else {
            return fail(request, "Unknown device_id. Use `iphone`.")
        }

        // Only enabled in the dedicated headless helper. The app retains its original API.
        if ProcessInfo.processInfo.environment["OIU_HEADLESS"] == "1" {
            switch request.operation {
            case "headless_identity":
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: String(ProcessInfo.processInfo.processIdentifier))
            case "ingest_frame":
                guard let text = request.text, let data = Data(base64Encoded: text) else {
                    return fail(request, "Invalid frame encoding.")
                }
                do {
                    let frame = try await frames.put(jpeg: data)
                    let controls = "{\"frameID\":\"\(frame.frameID)\",\"fps\":\(captureFramesPerSecond),\"boardEpoch\":\(receiver.boardEpoch),\"boardOpen\":\(receiver.boardOpen)}"
                    return BrokerResponse(requestID: request.requestID, ok: true, message: controls)
                } catch { return fail(request, "Invalid JPEG frame.") }
            case "capture_tap":
                if let x = request.x, let y = request.y { await frames.recordTap(x: x, y: y) }
                return BrokerResponse(requestID: request.requestID, ok: true, message: "Tap recorded.")
            case "capture_stop":
                await frames.setCaptureActive(false)
                return await handle(BrokerRequest(operation: "stop_control", deviceID: Self.deviceID))
            default: break
            }
            let reads = ["guide", "list_devices", "get_device_status", "get_latest_screenshot", "stop_control"]
            if !reads.contains(request.operation) {
                guard let expected = ProcessInfo.processInfo.environment["OIU_EXPECTED_CENTRAL"],
                      centralIdentifier == expected else {
                    hid.releaseAll()
                    return fail(request, "Pairing identity mismatch; explicitly bind the connected central before input.")
                }
            }
        }

        if request.operation == "guide" {
            return BrokerResponse(requestID: request.requestID, ok: true, message: Self.guideText)
        }

        if request.operation == "list_devices" {
            let state = await deviceStatus()
            let status = isConnected ? "connected" : (isAdvertising ? "advertising" : bluetoothState)
            return BrokerResponse(requestID: request.requestID, ok: true, message: "One iPhone slot is available.",
                                  devices: [DeviceSummary(deviceID: Self.deviceID, name: state.name,
                                                         transport: "BLE HID over GATT", status: status)])
        }

        if request.operation == "get_device_status" {
            return BrokerResponse(requestID: request.requestID, ok: true, message: "Current Open iPhone Use device status.", status: await deviceStatus())
        }

        if request.operation == "get_latest_screenshot" {
            guard let frame = await frames.latestFrame() else { return fail(request, "No iPhone frame has reached this Mac yet. Start the native Open iPhone Use screen broadcast on the iPhone.") }
            let age = await frames.ageMilliseconds(of: frame)
            let storeActive = await frames.isCaptureActive()
            let captureActive = age <= maximumFrameAgeMilliseconds && storeActive
            let captureMessage = await frames.error()
            let wire = ScreenshotFrameWire(frameID: frame.frameID, capturedAt: frame.capturedAt,
                                           ageMilliseconds: age, captureActive: captureActive,
                                           mimeType: "image/jpeg", jpeg: frame.jpeg)
            return BrokerResponse(requestID: request.requestID, ok: true,
                                  message: "Latest iPhone frame; age=\(age) ms; captureActive=\(captureActive)." + (captureMessage.map { " \($0)" } ?? ""),
                                  frame: wire)
        }

        if request.operation == "stop_control" {
            activeActionTask?.cancel()
            lastPointerAttempt = nil
            hid.releaseAll()
            let commandID = UUID().uuidString
            lastCommandID = commandID
            let canceled = queuedActions
            queuedActions.removeAll()
            for (queuedRequest, continuation) in canceled {
                continuation.resume(returning: fail(queuedRequest, "Canceled by stop_control.", commandID: UUID().uuidString))
            }
            return BrokerResponse(requestID: request.requestID, ok: true,
                                  message: "Released all keys and pointer buttons. HID advertising remains under app control.",
                                  commandID: commandID)
        }

        return await enqueueAction(request)
    }

    private func enqueueAction(_ request: BrokerRequest) async -> BrokerResponse {
        await withCheckedContinuation { continuation in
            queuedActions.append((request, continuation))
            processNextAction()
        }
    }

    private func processNextAction() {
        guard !processingAction, !queuedActions.isEmpty else { return }
        processingAction = true
        let (request, continuation) = queuedActions.removeFirst()
        activeActionTask = Task { @MainActor in
            let response = await performAction(request)
            continuation.resume(returning: response)
            processingAction = false
            processNextAction()
        }
    }

    private func performAction(_ request: BrokerRequest) async -> BrokerResponse {
        do {
            try Task.checkCancellation()
            // A missing frame ID is an explicit HID diagnostic: send the report without
            // requiring a fresh capture, so transport can be tested independently.
            let selfCalibrating = ["pointer_home", "pointer_calibrate", "pointer_probe",
                                   "pointer_locate", "prepare_session", "ensure_session", "set_page_board"]
            if request.expectedFrameID != nil, !selfCalibrating.contains(request.operation) {
                try await frames.validateControlFrame(expectedFrameID: request.expectedFrameID,
                                                      maximumAgeMilliseconds: maximumFrameAgeMilliseconds)
            }
        } catch {
            hid.releaseAll()
            return fail(request, error.localizedDescription)
        }
        guard isConnected else { return fail(request, "iPhone is not connected as a BLE HID device.") }

        let commandID = UUID().uuidString
        lastCommandID = commandID
        if !["pointer_tap", "pointer_retry_tap"].contains(request.operation) { lastPointerAttempt = nil }
        do {
            switch request.operation {
            case "move_pointer":
                let dx = request.dx ?? 0
                let dy = request.dy ?? 0
                guard (-127...127).contains(dx), (-127...127).contains(dy) else { return fail(request, "Pointer deltas must be between -127 and 127.") }
                pointerUnits = nil
                try await hid.moveSmooth(dx: dx, dy: dy, steps: request.steps, intervalMilliseconds: request.intervalMilliseconds)
            case "click":
                guard try await locatePointer() != nil else { throw PointerControlError.notFound }
                try await hid.click(button: request.button ?? "left")
            case "advance_feed":
                try await advanceFeed(items: request.dy ?? 1, wheelPerItem: request.steps ?? 120,
                                      mode: request.profile ?? "swipe")
                return await verified(request, "Feed gesture sent. Inspect the account/title to confirm one item changed; item count is not measured. Do not infer success from moving video pixels.", commandID: commandID)
            case "swipe":
                try await swipeScreen(direction: request.profile ?? "up")
                return await verified(request, "Swipe sent; inspect the resulting screen.", commandID: commandID)
            case "pointer_retry_tap":
                guard let previous = lastPointerAttempt, request.previousCommandID == previous.commandID,
                      Date().timeIntervalSince(previous.at) < 60,
                      let frameID = request.expectedFrameID,
                      let observedX = request.observedX, let observedY = request.observedY,
                      let correction = PointerRetry.correction(targetX: previous.x, targetY: previous.y,
                                                               observedX: observedX, observedY: observedY),
                      let span = pointerCalibration.span else {
                    return fail(request, "Retry requires the last tap command_id, a fresh post-tap frame and an observed landing within 8% of its target. Never guess a landing from an unchanged screen.")
                }
                try await frames.validateControlFrame(expectedFrameID: frameID)
                guard let observedAt = await frames.frameTime(for: frameID), observedAt >= previous.at else {
                    return fail(request, "Retry feedback frame predates the tap. Read a fresh post-tap screenshot.")
                }
                try await validateCalibrationScreen()
                lastPointerAttempt = nil // one retry per measured attempt
                let ux = previous.unitsX + Int((correction.x * Double(span.width)).rounded())
                let uy = previous.unitsY + Int((correction.y * Double(span.height)).rounded())
                try await movePointerToUnits(x: ux, y: uy)
                try await correctPointerAim(previous.x, previous.y)
                try await hid.click(button: "left")
                return await verified(request, "One bounded correction sent using the supplied observed landing. Inspect UI outcome; calibration unchanged.", commandID: commandID)
            case "toggle_playback":
                try await togglePlayback()
                return await verified(request, "Toggled playback.", commandID: commandID)
            case "scroll":
                try await scrollScreen(wheel: request.dy ?? 0)
                return await verified(request, "Scrolled by \(request.dy ?? 0).", commandID: commandID)
            case "move_assistive_touch":
                try await moveAssistiveTouch(dx: request.dx ?? -600, dy: request.dy ?? -600)
                return await verified(request, "Dragged the AssistiveTouch button away.", commandID: commandID)
            case "pointer_home":
                try await pointerHome()
                return await verified(request, "Pointer homed into the top-left corner.", commandID: commandID)
            case "pointer_calibrate":
                let span = try await calibratePointer()
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: "Pointer calibrated: \(span.width)x\(span.height) units across the screen.",
                                      commandID: commandID)
            case "pointer_quick_probe":
                let span = try await quickProbePointer()
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: "Two-click pointer estimate measured: \(span.width)x\(span.height) units.",
                                      commandID: commandID)
            case "pointer_probe":
                if let requested = request.profile, let parsed = PointerMotion.Profile(rawValue: requested) {
                    pointerMotionProfile = parsed
                }
                let span = try await probePointer()
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: "Pointer probed from the calibration board: \(span.width)x\(span.height) units.",
                                      commandID: commandID)
            case "pointer_taps":
                let taps = await frames.taps(since: Date().addingTimeInterval(-120))
                let list = taps.map { String(format: "(%.4f, %.4f)", $0.x, $0.y) }.joined(separator: " ")
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: taps.isEmpty ? "No taps recorded in the last two minutes." : "Taps: \(list)",
                                      commandID: commandID)
            case "ensure_session":
                let summary = try await ensureSession()
                let snapshot = await handle(BrokerRequest(operation: "get_latest_screenshot", deviceID: Self.deviceID))
                return BrokerResponse(requestID: request.requestID, ok: snapshot.ok, message: summary,
                                      commandID: commandID, frame: snapshot.frame)
            case "prepare_session":
                let summary = try await prepareSession()
                return await verified(request, summary, commandID: commandID)
            case "set_page_board":
                setPageBoard(open: request.aim ?? false)
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: "Calibration board \(request.aim == true ? "open" : "closed") requested.", commandID: commandID)
            case "pointer_tap_probed":
                guard let x = request.x, let y = request.y else { return fail(request, "pointer_tap_probed needs x and y as screen fractions.") }
                guard (0...1).contains(x), (0...1).contains(y) else { return fail(request, "x and y must be screen fractions between 0 and 1.") }
                guard let tap = try await probeTap(x: x, y: y, attempts: request.steps ?? 2) else {
                    throw PointerControlError.noTaps
                }
                return await verified(request, String(format: "Tapped (%.3f, %.3f); the board landed it at (%.4f, %.4f).",
                                                      x, y, tap.x, tap.y), commandID: commandID)
            case "pointer_locate":
                guard let point = try await locatePointer() else { throw PointerControlError.notFound }
                return BrokerResponse(requestID: request.requestID, ok: true,
                                      message: String(format: "Pointer found at (%.3f, %.3f).", point.x, point.y),
                                      commandID: commandID)
            case "pointer_tap":
                guard let x = request.x, let y = request.y else { return fail(request, "pointer_tap needs x and y as screen fractions.") }
                guard (0...1).contains(x), (0...1).contains(y) else { return fail(request, "x and y must be screen fractions between 0 and 1.") }
                lastPointerAttempt = nil
                let outcome = try await tapWithOutcome(x: x, y: y)
                if let units = pointerUnits {
                    lastPointerAttempt = PointerAttempt(commandID: commandID, x: x, y: y,
                                                        unitsX: units.x, unitsY: units.y, at: Date())
                }
                // Report the measured landing, not the aim: a caller that only hears "tapped"
                // cannot tell a hit from a miss and will retry the same spot forever.
                let message: String
                if let landed = outcome.landed {
                    message = String(format: "Tapped (%.3f, %.3f); the board landed it at (%.3f, %.3f), miss %.3f.",
                                     x, y, landed.x, landed.y, outcome.miss)
                } else {
                    message = String(format: "Tapped (%.3f, %.3f); landing not measured (board closed).", x, y)
                }
                return await verified(request, message, commandID: commandID)
            case "pointer_jump", "pointer_jump_tap":
                guard let x = request.x, let y = request.y, x.isFinite, y.isFinite,
                      (0...1).contains(x), (0...1).contains(y) else { return fail(request, "\(request.operation) needs x and y as screen fractions.") }
                if request.operation == "pointer_jump" {
                    try await pointerJump(x: x, y: y)
                } else {
                    try await pointerJumpTap(x: x, y: y)
                }
                return await verified(request, String(format: "\(request.operation == "pointer_jump" ? "Jumped to" : "Jump-tapped") (%.3f, %.3f).", x, y), commandID: commandID)
            case "pointer_move":
                guard let x = request.x, let y = request.y else { return fail(request, "pointer_move needs x and y as screen fractions.") }
                guard (0...1).contains(x), (0...1).contains(y) else { return fail(request, "x and y must be screen fractions between 0 and 1.") }
                try await pointerMove(x: x, y: y, aim: request.aim ?? false)
                return await verified(request, String(format: "Pointer moved to (%.3f, %.3f).", x, y), commandID: commandID)
            case "type_text":
                guard let text = request.text else { return fail(request, "Missing text.") }
                try await hid.typeText(text)
            case "send_key": try hid.sendKey(request.key ?? "")
            default: return fail(request, "Unknown Open iPhone Use operation.")
            }
            refreshBluetoothState()
            return await verified(request, "Action sent to the connected HID device.", commandID: commandID)
        } catch {
            hid.releaseAll()
            lastError = error.localizedDescription
            refreshBluetoothState()
            return fail(request, error.localizedDescription, commandID: commandID)
        }
    }

    /// Everything another agent needs to drive this phone, in one place.
    public static let guideText = AgentWorkflow.instructions + """

    Intent -> tool:
      Open an app: cmd+h, cmd+space, cmd+a, type_text <name>, inspect, enter.
      Click UI: pointer_tap at whole-screen fractions, not Safari viewport fractions.
      List/grid scroll: scroll(dy); sensitivity varies; inspect the result.
      Next reel / TikTok / Shorts: advance_feed(dy: 1), previous dy: -1.
        One transition per call; inspect account/title before another. Default is a
        centered press-drag-release swipe. mode=wheel is an explicit fallback, with
        steps=120 default. Wheel amount is NOT a guaranteed number of items.
        Never pause/resume by clicking wherever the pointer happened to stop.
      Gallery/slider: swipe(direction: up/down/left/right), then inspect.
      Pause/resume: toggle_playback sends a centered tap; verify app-specific state.
      Wrong tap: first read a fresh screenshot. Only if the actual landing is visible,
        pointer_retry_tap(previous_command_id, expected_frame_id, observed_x, observed_y)
        sends one bounded correction. Unchanged UI alone does not locate the miss.
        Do not retry an uncertain purchase/post/follow/submission.
      AssistiveTouch obstruction: move_assistive_touch; inspect before proceeding.

    Calibration: sharing start automatically checks it. ensure_session also checks it,
    reuses a device/motion-version/orientation-bound model or estimates it with two measured clicks
    on the visible board. No profile scan or extra hover tour runs during startup.
    Status distinguishes unvalidated estimates from heldOut validation. pointer_probe
    measures held-out targets separately and reports heldOut. Ordinary app taps cannot
    teach calibration unless a real landing was observed; video motion is not feedback.
    A board=true background page is insufficient: the visible green border must be found.
    If board geometry is absent, bring the existing Safari sharing tab to the foreground.
    No pairing/unlock/system broadcast consent can be bypassed.
    Legacy pointer_jump aliases use the same calibrated movement as pointer_move/tap.
    """

    /// Input changes the screen, and looking after every action is how a wrong turn is caught
    /// early instead of three steps later, so every input action returns the frame it produced.
    func verified(_ request: BrokerRequest, _ message: String, commandID: String?,
                  settleMilliseconds: Int = 250) async -> BrokerResponse {
        // Transmission fence first. A frame captured while movement is still queued is
        // not post-action evidence, even when its timestamp is newer than the tool call.
        do { try await hid.waitForReportsToDrain() }
        catch { return fail(request, error.localizedDescription, commandID: commandID) }
        let completedAt = Date()
        try? await Task.sleep(for: .milliseconds(settleMilliseconds))
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline {
            if let next = await frames.latestFrame(), next.capturedAt > completedAt { break }
            try? await Task.sleep(for: .milliseconds(60))
        }
        guard let frame = await frames.latestFrame(), frame.capturedAt > completedAt else {
            return BrokerResponse(requestID: request.requestID, ok: true,
                                  message: message + " No post-action frame arrived; inspect a fresh screenshot before continuing.", commandID: commandID)
        }
        let age = await frames.ageMilliseconds(of: frame)
        let storeActive = await frames.isCaptureActive()
        let wire = ScreenshotFrameWire(frameID: frame.frameID, capturedAt: frame.capturedAt,
                                       ageMilliseconds: age,
                                       captureActive: age <= maximumFrameAgeMilliseconds && storeActive,
                                       mimeType: "image/jpeg", jpeg: frame.jpeg)
        let settleHint = ["swipe", "advance_feed", "scroll"].contains(request.operation)
            ? " UI may still be animating; take a fresh screenshot before targeting." : ""
        return BrokerResponse(requestID: request.requestID, ok: true, message: message + settleHint,
                              commandID: commandID, frame: wire)
    }

    private func fail(_ request: BrokerRequest, _ message: String, commandID: String? = nil) -> BrokerResponse {
        BrokerResponse(requestID: request.requestID, ok: false, message: message,
                       commandID: commandID ?? UUID().uuidString)
    }
}
