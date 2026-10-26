@preconcurrency import CoreBluetooth
import Foundation
import ObjCExceptionCatcher

public enum BLEHIDError: Error, LocalizedError {
    case unavailable(String)
    case notConnected
    case sendBackpressure
    case advertisingFailed(String)

    public var errorDescription: String? {
        switch self {
        case .unavailable(let reason): "Bluetooth HID is unavailable: \(reason)"
        case .notConnected: "No iPhone is connected to Open iPhone Use BLE HID."
        case .sendBackpressure: "The iPhone is not accepting HID reports yet."
        case .advertisingFailed(let reason): "Bluetooth advertisement failed: \(reason)"
        }
    }
}

@MainActor
public final class BLEHIDPeripheral: NSObject {
    public static let deviceID = "iphone"
    private static let hidService = CBUUID(string: "00001812-0000-1000-8000-00805F9B34FB")
    private static let hidInformation = CBUUID(string: "2A4A")
    private static let reportMap = CBUUID(string: "2A4B")
    private static let hidControlPoint = CBUUID(string: "2A4C")
    private static let report = CBUUID(string: "2A4D")
    private static let protocolMode = CBUUID(string: "2A4E")
    private static let reportReference = CBUUID(string: "2908")

    private var manager: CBPeripheralManager?
    private var keyboardInput: CBMutableCharacteristic?
    private var mouseInput: CBMutableCharacteristic?
    private var subscribedCentral: CBCentral?
    private var subscribedReportIDs: Set<UInt8> = []
    private var queuedReports = HIDReportQueue()
    private var typingTask: Task<Void, any Error>?
    private var motionTask: Task<Void, any Error>?
    private var currentButtons: UInt8 = 0

    public private(set) var state = "notStarted"
    public private(set) var isAdvertising = false
    public private(set) var isConnected = false
    public var isTyping: Bool { typingTask != nil }
    public private(set) var centralIdentifier: String?
    public private(set) var lastError: String?
    public private(set) var sentReportCount = 0
    public private(set) var rejectedReportCount = 0
    public var onChange: (() -> Void)?

    public override init() { super.init() }

    public func startAdvertising() {
        if manager != nil {
            guard state == "bluetoothInitializationTimedOut" else { return }
            stopAdvertising()
        }
        state = "starting"
        lastError = nil
        manager = CBPeripheralManager(delegate: self, queue: .main, options: [CBPeripheralManagerOptionShowPowerAlertKey: true])
        onChange?()

        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.state == "starting", let manager = self.manager else { return }
            if manager.state != .unknown {
                self.peripheralManagerDidUpdateState(manager)
            } else if CBManager.authorization == .notDetermined {
                self.state = "waitingForBluetoothPermission"
                self.lastError = "Allow Bluetooth for Open iPhone Use in the macOS prompt or Privacy & Security settings."
                self.onChange?()
            } else {
                self.state = "bluetoothInitializationTimedOut"
                self.lastError = "CoreBluetooth did not report a state (manager state \(manager.state.rawValue), authorization \(CBManager.authorization.rawValue))."
                self.onChange?()
            }
        }
    }

    public func stopAdvertising() {
        typingTask?.cancel()
        typingTask = nil
        releaseAll()
        manager?.stopAdvertising()
        manager?.removeAllServices()
        manager = nil
        keyboardInput = nil
        mouseInput = nil
        subscribedCentral = nil
        subscribedReportIDs.removeAll()
        queuedReports.removeAll()
        isAdvertising = false
        isConnected = false
        centralIdentifier = nil
        state = "stopped"
        onChange?()
    }

    public func move(dx: Int, dy: Int) throws {
        try send(HIDReportEncoder.mouse(buttons: currentButtons, dx: dx, dy: dy),
                 reportID: HIDReportEncoder.mouseReportID)
    }

    /// Slams the pointer into the top-left corner so its position becomes known again.
    ///
    /// iOS clamps the pointer at the screen edges, so repeating a large negative move is a
    /// deterministic home: further movement in that direction changes nothing. This cancels
    /// accumulated drift, establishing a known origin before counting relative units.
    public func home() async throws {
        guard isConnected else { throw BLEHIDError.notConnected }
        // Reach the left edge before moving up, avoiding the middle of the status edge.
        currentButtons = 0
        try send(HIDReportEncoder.mouse(), reportID: HIDReportEncoder.mouseReportID)
        let left = PointerMotion.Delta(dx: -PointerMotion.maximumDelta, dy: 0)
        let up = PointerMotion.Delta(dx: 0, dy: -PointerMotion.maximumDelta)
        try await executeMotion(.init(deltas: Array(repeating: left, count: Self.homeRepeats)
                                      + Array(repeating: up, count: Self.homeRepeats),
                                      intervalMilliseconds: 8))
    }

    /// Enough repeats that pointer acceleration cannot leave the pointer short of the corner.
    public static let homeRepeats = 8

    /// Moves the pointer as a paced sequence of small reports instead of one jump.
    ///
    /// iOS accelerates each report, so many small deltas read as smooth motion. A new
    /// request cancels the previous glide so the pointer never fights itself.
    public func moveSmooth(dx: Int, dy: Int, steps: Int? = nil, intervalMilliseconds: Int? = nil) async throws {
        let plan = PointerMotion.plan(dx: dx, dy: dy, steps: steps, intervalMilliseconds: intervalMilliseconds)
        try await executeMotion(plan)
    }

    public func moveCalibrated(dx: Int, dy: Int, profile: PointerMotion.Profile = .turbo32) async throws {
        guard abs(dx) <= 20_000, abs(dy) <= 20_000 else { throw HIDReportError.movementOutOfRange }
        try await executeMotion(PointerMotion.calibratedPlan(dx: dx, dy: dy, profile: profile))
    }

    private func executeMotion(_ plan: PointerMotion.Plan) async throws {
        try Task.checkCancellation()
        guard !plan.deltas.isEmpty else { return }
        guard isConnected else { throw BLEHIDError.notConnected }

        let task = Task { @MainActor in
            for delta in plan.deltas {
                try Task.checkCancellation()
                try await self.waitForCapacity()
                try Task.checkCancellation()
                try self.send(HIDReportEncoder.mouse(buttons: self.currentButtons, dx: delta.dx, dy: delta.dy),
                              reportID: HIDReportEncoder.mouseReportID)
                // Draining after every report makes each one cost a full BLE round trip, which
                // was measured at ~20 ms and made the report size irrelevant: 850 units took
                // 8.4 s whether the profile sent 2 units per report or 32. `waitForCapacity`
                // already applies backpressure at the queue's limit, so the queue can run ahead.
                if plan.intervalMilliseconds > 0 {
                    try await Task.sleep(for: .milliseconds(plan.intervalMilliseconds))
                }
            }
        }
        motionTask?.cancel()
        motionTask = task
        do {
            try await task.value
            // One fence per motion, never one per report. The next click/home must not
            // overtake buffered movement, and its screenshot must follow transmission.
            try await waitForReportsToDrain()
        } catch is CancellationError { throw CancellationError() }
        if motionTask == task { motionTask = nil }
    }

    /// Waits for CoreBluetooth to accept reports instead of dropping motion mid-glide.
    private func waitForCapacity() async throws {
        let deadline = Date().addingTimeInterval(2)
        while queuedReports.isFull {
            guard Date() < deadline else { throw BLEHIDError.sendBackpressure }
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    public func waitForReportsToDrain() async throws {
        let deadline = Date().addingTimeInterval(8)
        while !queuedReports.pending.isEmpty {
            try Task.checkCancellation()
            guard isConnected else { throw BLEHIDError.notConnected }
            guard Date() < deadline else { throw BLEHIDError.sendBackpressure }
            try await Task.sleep(for: .milliseconds(2))
        }
    }

    public var pendingReportCount: Int { queuedReports.pending.count }

    public func swipe(dx: Int, dy: Int, profile: PointerMotion.Profile) async throws {
        guard isConnected else { throw BLEHIDError.notConnected }
        currentButtons = try HIDReportEncoder.buttonMask("left")
        do {
            try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
            try await waitForReportsToDrain()
            try await Task.sleep(for: .milliseconds(50))
            let plan = PointerMotion.calibratedPlan(dx: dx, dy: dy, profile: profile)
            let interval = max(8, 220 / max(1, plan.deltas.count))
            try await executeMotion(.init(deltas: plan.deltas, intervalMilliseconds: interval))
            currentButtons = 0
            try send(HIDReportEncoder.mouse(), reportID: HIDReportEncoder.mouseReportID)
            try await waitForReportsToDrain()
        } catch { releaseAll(); throw error }
    }

    public func cancelMotion() {
        motionTask?.cancel()
        motionTask = nil
    }

    /// Scrolls the focused scroll view. Negative moves the content up.
    ///
    /// AssistiveTouch turns wheel reports into the same scroll gestures a trackpad produces, so
    /// this is how a long feed or a settings list is paged without hunting for a drag handle.
    public func scroll(wheel: Int) async throws {
        try Task.checkCancellation()
        guard isConnected else { throw BLEHIDError.notConnected }
        var remaining = wheel
        while remaining != 0 {
            let step = max(-PointerMotion.maximumDelta, min(PointerMotion.maximumDelta, remaining))
            try await waitForCapacity()
            try send(HIDReportEncoder.mouse(buttons: currentButtons, wheel: step),
                     reportID: HIDReportEncoder.mouseReportID)
            remaining -= step
        }
        // A zero report ends the scroll, the same way releasing a wheel does.
        try await waitForCapacity()
        try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
        try await waitForReportsToDrain()
    }

    /// Drags the pointer from its current spot to a new one with the button held.
    ///
    /// The AssistiveTouch menu opens over whatever is being driven and there is no way to close
    /// it from the keyboard, so the reliable recovery is to move it out of the way: press, walk
    /// to an empty corner, release.
    public func dragTo(dx: Int, dy: Int, profile: PointerMotion.Profile = .turbo32) async throws {
        guard isConnected else { throw BLEHIDError.notConnected }
        currentButtons |= try HIDReportEncoder.buttonMask("left")
        try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
        do {
            try await executeMotion(PointerMotion.calibratedPlan(dx: dx, dy: dy, profile: profile))
            currentButtons &= ~(try HIDReportEncoder.buttonMask("left"))
            try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
            try await waitForReportsToDrain()
        } catch {
            releaseAll()
            throw error
        }
    }

    public func click(button: String) async throws {
        try Task.checkCancellation()
        let mask = try HIDReportEncoder.buttonMask(button)
        currentButtons |= mask
        do {
            try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
            // A short physical press; zero-length press/release pairs are not a
            // substitute for a measured touch lifecycle on every iOS device.
            try await waitForReportsToDrain()
            try await Task.sleep(for: .milliseconds(30))
            currentButtons &= ~mask
            try send(HIDReportEncoder.mouse(buttons: currentButtons), reportID: HIDReportEncoder.mouseReportID)
            try await waitForReportsToDrain()
        } catch {
            releaseAll()
            throw error
        }
    }

    public func typeText(_ text: String) async throws {
        let strokes = try HIDReportEncoder.text(text) // Validate the full string before the first report.
        guard isConnected else { throw BLEHIDError.notConnected }
        typingTask?.cancel()
        let task = Task { @MainActor in
            for stroke in strokes {
                try Task.checkCancellation()
                try self.send(HIDReportEncoder.keyboard(modifier: stroke.modifier, usages: [stroke.usage]),
                              reportID: HIDReportEncoder.keyboardReportID)
                try await Task.sleep(for: .milliseconds(24))
                try self.send(HIDReportEncoder.keyboard(modifier: 0, usages: []), reportID: HIDReportEncoder.keyboardReportID)
                try await Task.sleep(for: .milliseconds(24))
            }
        }
        typingTask = task
        defer { typingTask = nil }
        try await task.value
    }

    public func sendKey(_ key: String) throws {
        let stroke = try HIDReportEncoder.chord(key)
        try send(HIDReportEncoder.keyboard(modifier: stroke.modifier, usages: [stroke.usage]), reportID: HIDReportEncoder.keyboardReportID)
        try send(HIDReportEncoder.keyboard(modifier: 0, usages: []), reportID: HIDReportEncoder.keyboardReportID)
    }

    public func releaseAll() {
        cancelMotion()
        typingTask?.cancel()
        typingTask = nil
        currentButtons = 0
        queuedReports.removeAll()
        if isConnected {
            try? send(HIDReportEncoder.keyboard(modifier: 0, usages: []), reportID: HIDReportEncoder.keyboardReportID)
            try? send(HIDReportEncoder.mouse(), reportID: HIDReportEncoder.mouseReportID)
        }
    }

    private func send(_ data: Data, reportID: UInt8) throws {
        guard manager != nil, subscribedCentral != nil else { throw BLEHIDError.notConnected }
        guard subscribedReportIDs.contains(reportID) else {
            throw BLEHIDError.unavailable("the iPhone has not subscribed to report \(reportID)")
        }
        try queuedReports.append(HIDInputReport(reportID: reportID, payload: data))
        flushReports()
    }

    private func flushReports() {
        guard let manager, let central = subscribedCentral else { return }
        let keyboard = keyboardInput
        let mouse = mouseInput
        let subscriptions = subscribedReportIDs
        queuedReports.drain { report in
            let characteristic = report.reportID == HIDReportEncoder.keyboardReportID ? keyboard : mouse
            guard subscriptions.contains(report.reportID), let characteristic else { return false }
            let accepted = manager.updateValue(report.payload, for: characteristic, onSubscribedCentrals: [central])
            if accepted { sentReportCount += 1 } else { rejectedReportCount += 1 }
            return accepted
        }
    }

    private func reportReferenceDescriptor(id: UInt8, type: UInt8) -> CBMutableDescriptor {
        CBMutableDescriptor(type: Self.reportReference, value: Data([id, type]))
    }

    private func publishHIDService() {
        guard let manager else { return }

        let hidInfo = CBMutableCharacteristic(type: Self.hidInformation, properties: [.read],
                                              value: Data([0x11, 0x01, 0x00, 0x02]), permissions: [.readEncryptionRequired])
        let map = CBMutableCharacteristic(type: Self.reportMap, properties: [.read],
                                          value: Self.reportDescriptor, permissions: [.readEncryptionRequired])
        let control = CBMutableCharacteristic(type: Self.hidControlPoint, properties: [.writeWithoutResponse],
                                              value: nil, permissions: [.writeEncryptionRequired])
        let mode = CBMutableCharacteristic(type: Self.protocolMode, properties: [.read, .writeWithoutResponse],
                                           value: nil, permissions: [.readEncryptionRequired, .writeEncryptionRequired])

        // CoreBluetooth requires characteristics with cached values to be read-only.
        // Supply the initial all-released reports in didReceiveRead instead.
        let keyboard = CBMutableCharacteristic(type: Self.report, properties: [.read, .notifyEncryptionRequired],
                                               value: nil,
                                               permissions: [.readEncryptionRequired])
        keyboard.descriptors = [reportReferenceDescriptor(id: HIDReportEncoder.keyboardReportID, type: 1)]
        let mouse = CBMutableCharacteristic(type: Self.report, properties: [.read, .notifyEncryptionRequired],
                                            value: nil, permissions: [.readEncryptionRequired])
        mouse.descriptors = [reportReferenceDescriptor(id: HIDReportEncoder.mouseReportID, type: 1)]
        keyboardInput = keyboard
        mouseInput = mouse

        let service = CBMutableService(type: Self.hidService, primary: true)
        service.characteristics = [hidInfo, map, control, mode, keyboard, mouse]
        if let exception = OpenIPhoneUseTryAddService(manager, service) {
            state = "servicePublishFailed"
            lastError = exception
            onChange?()
            return
        }
        state = "publishingHIDService"
        onChange?()
    }

    private static let reportDescriptor = Data([
        0x05, 0x01,       // Usage Page (Generic Desktop)
        0x09, 0x06,       // Usage (Keyboard)
        0xA1, 0x01,       // Collection (Application)
        0x85, 0x01,       // Report ID (1)
        0x05, 0x07,       // Usage Page (Keyboard)
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02, // Modifier byte
        0x95, 0x01, 0x75, 0x08, 0x81, 0x01, // Reserved byte
        0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
        0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, // Six key slots
        0xC0,
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, // Mouse application
        0x85, 0x02,       // Report ID (2)
        0x09, 0x01, 0xA1, 0x00, // Pointer physical collection
        0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01,
        0x95, 0x03, 0x75, 0x01, 0x81, 0x02, // Three buttons
        0x95, 0x01, 0x75, 0x05, 0x81, 0x01, // Padding
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
        0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06, // X/Y/wheel
        0xC0, 0xC0,
    ])
}

extension BLEHIDPeripheral: @preconcurrency CBPeripheralManagerDelegate {
    public func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        switch peripheral.state {
        case .poweredOn:
            state = "poweredOn"
            publishHIDService()
        case .poweredOff: state = "bluetoothOff"
        case .unauthorized: state = "bluetoothPermissionDenied"
        case .unsupported: state = "blePeripheralUnsupported"
        case .resetting: state = "resetting"
        case .unknown: state = "unknown"
        @unknown default: state = "unknown"
        }
        onChange?()
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error {
            state = "servicePublishFailed"
            lastError = error.localizedDescription
            onChange?()
            return
        }
        peripheral.startAdvertising([
            CBAdvertisementDataLocalNameKey: ProcessInfo.processInfo.environment["OIU_DEVICE_NAME"] ?? "Open iPhone Use",
            CBAdvertisementDataServiceUUIDsKey: [Self.hidService],
        ])
    }

    public func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        if let error {
            state = "advertisingFailed"
            lastError = error.localizedDescription
            isAdvertising = false
        } else {
            state = "advertising"
            lastError = nil
            isAdvertising = true
        }
        onChange?()
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                                  didSubscribeTo characteristic: CBCharacteristic) {
        // A headless device route is permanently bound to an owner-selected central.
        if let expected = ProcessInfo.processInfo.environment["OIU_EXPECTED_CENTRAL"],
           central.identifier.uuidString != expected { return }
        if let id = inputReportID(for: characteristic),
           subscribedCentral == nil || subscribedCentral?.identifier == central.identifier {
            subscribedReportIDs.insert(id)
            subscribedCentral = central
            centralIdentifier = central.identifier.uuidString
            isConnected = true
            state = "connected"
            onChange?()
        }
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                                  didUnsubscribeFrom characteristic: CBCharacteristic) {
        if subscribedCentral?.identifier == central.identifier,
           let id = inputReportID(for: characteristic) {
            subscribedReportIDs.remove(id)
            releaseAll()
            guard subscribedReportIDs.isEmpty else { return }
            subscribedCentral = nil
            centralIdentifier = nil
            isConnected = false
            state = isAdvertising ? "advertising" : "disconnected"
            onChange?()
        }
    }

    public func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        flushReports()
    }

    private func inputReportID(for characteristic: CBCharacteristic) -> UInt8? {
        if characteristic === keyboardInput { return HIDReportEncoder.keyboardReportID }
        if characteristic === mouseInput { return HIDReportEncoder.mouseReportID }
        return nil
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        let value: Data
        if request.characteristic.uuid == Self.hidInformation {
            value = Data([0x11, 0x01, 0x00, 0x02])
        } else if request.characteristic.uuid == Self.protocolMode {
            value = Data([0x01])
        } else if request.characteristic.uuid == Self.reportMap {
            value = Self.reportDescriptor
        } else if request.characteristic.uuid == Self.report,
                  request.characteristic === keyboardInput {
            value = HIDReportEncoder.keyboard(modifier: 0, usages: [])
        } else if request.characteristic.uuid == Self.report,
                  request.characteristic === mouseInput,
                  let report = try? HIDReportEncoder.mouse() {
            value = report
        } else {
            peripheral.respond(to: request, withResult: .attributeNotFound)
            return
        }
        guard request.offset <= value.count else {
            peripheral.respond(to: request, withResult: .invalidOffset)
            return
        }
        request.value = value.subdata(in: request.offset..<value.count)
        peripheral.respond(to: request, withResult: .success)
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        for request in requests {
            switch request.characteristic.uuid {
            case Self.protocolMode, Self.hidControlPoint: peripheral.respond(to: request, withResult: .success)
            default: peripheral.respond(to: request, withResult: .writeNotPermitted)
            }
        }
    }
}
