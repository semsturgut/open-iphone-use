@preconcurrency import CoreBluetooth
import Foundation
import CryptoKit
import OpenIPhoneUseCore
import ObjCExceptionCatcher

/// One published HID service, many subscribed centrals. Every send names exactly one peer.
/// The legacy single-phone peripheral/controller remain unchanged for the optional app.
@MainActor
final class HeadlessRadio: NSObject, @preconcurrency CBPeripheralManagerDelegate {
    private var manager: CBPeripheralManager?
    private var publishedService: CBMutableService?
    private var advertisingPending = false
    private var reconnectTask: Task<Void, Never>?
    private var keyboard: CBMutableCharacteristic?
    private var mouse: CBMutableCharacteristic?
    private var peers: [String: CBCentral] = [:]
    private var subscriptions: [String: Set<Int>] = [:]
    private let nameResolver = PeerNameResolver()
    private lazy var reportQueue = PerCentralReportQueue { [weak self] id, report, payload in
        guard let self, let manager = self.manager, let peer = self.peers[id],
              self.subscriptions[id]?.contains(report) == true,
              let characteristic = report == 1 ? self.keyboard : self.mouse else { return false }
        return manager.updateValue(payload, for: characteristic, onSubscribedCentrals: [peer])
    }
    private var broker: UnixSocketBroker?
    private var state = "starting"
    private var lastError: String?
    // Keep the full Bluetooth Base UUID: macOS rejects the short HID service UUID.
    private let hid = CBUUID(string: "00001812-0000-1000-8000-00805F9B34FB")

    func start() throws {
        broker = UnixSocketBroker { [weak self] request in
            guard let self else { return BrokerResponse(requestID: request.requestID, ok: false, message: "Radio stopped") }
            return await self.handle(request)
        }
        try broker?.start()
        if ProcessInfo.processInfo.environment["OIU_DISABLE_BLUETOOTH"] == "1" { state = "bluetoothDisabledForTest"; return }
        nameResolver.start()
        manager = CBPeripheralManager(delegate: self, queue: .main,
                                      options: [CBPeripheralManagerOptionShowPowerAlertKey: true,
                                                CBPeripheralManagerOptionRestoreIdentifierKey: restorationIdentifier])
        reconnectTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(3))
                guard !Task.isCancelled, let self else { return }
                if let manager = self.manager { self.resumeAdvertising(manager) }
            }
        }
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.state == "starting" else { return }
            self.state = CBManager.authorization == .notDetermined ? "waitingForBluetoothPermission" : "bluetoothInitializationTimedOut"
            self.lastError = "CoreBluetooth has not initialized. Approve the host Bluetooth prompt, enable Bluetooth, and restart outside a restricted sandbox if its XPC service is unavailable."
        }
    }

    // Stable across helper launches; separate state roots never share a restoration identity.
    private var restorationIdentifier: String {
        let directory = ProcessInfo.processInfo.environment["OIU_SUPPORT_DIRECTORY"] ?? "default"
        let digest = SHA256.hash(data: Data(directory.utf8)).map { String(format: "%02x", $0) }.joined()
        return "dev.openiphoneuse.hid." + digest
    }

    func stop() async {
        reconnectTask?.cancel(); reconnectTask = nil
        for id in Array(peers.keys) { await release(id) }
        nameResolver.stop()
        manager?.stopAdvertising()
        // Preserve the restored GATT database; explicit removal forces a service change.
        broker?.stop()
        manager = nil
    }

    private func handle(_ request: BrokerRequest) async -> BrokerResponse {
        do {
            let result: [String: Any]
            switch request.operation {
            case "headless_identity":
                return BrokerResponse(requestID: request.requestID, ok: true, message: String(ProcessInfo.processInfo.processIdentifier))
            case "radio_status":
                result = ["state": state, "error": lastError ?? "", "peers": peers.keys.sorted().map {
                    let name = nameResolver.name(peers[$0]!.identifier)
                    return ["id": $0, "reports": Array(subscriptions[$0] ?? []).sorted(),
                            "name": name.0, "name_source": name.1] as [String: Any]
                }]
            case "radio_send":
                guard let peer = request.key, let report = request.dx, let text = request.text,
                      let payload = Data(base64Encoded: text),
                      (report == 1 && payload.count == 8) || (report == 2 && payload.count == 4) else {
                    throw RadioError.message("Invalid peer, report ID or HID payload length")
                }
                try await send(peer, report: report, payload: payload)
                result = ["sent": true]
            case "radio_release":
                guard let peer = request.key else { throw RadioError.message("Peer required") }
                await release(peer)
                result = ["released": true]
            default: throw RadioError.message("Unknown radio operation")
            }
            let data = try JSONSerialization.data(withJSONObject: result)
            return BrokerResponse(requestID: request.requestID, ok: true, message: String(decoding: data, as: UTF8.self))
        } catch {
            return BrokerResponse(requestID: request.requestID, ok: false, message: error.localizedDescription)
        }
    }

    private func send(_ id: String, report: Int, payload: Data) async throws {
        guard manager != nil, let peer = peers[id], subscriptions[id]?.contains(report) == true,
              (report == 1 ? keyboard : mouse) != nil else {
            throw RadioError.message("iPhone disconnected or not subscribed to both HID reports; pair and enable AssistiveTouch")
        }
        guard payload.count <= peer.maximumUpdateValueLength else { throw RadioError.message("Bluetooth MTU too small") }
        try await reportQueue.send(peer: id, report: report, payload: payload)
    }

    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        reportQueue.ready()
    }

    private func release(_ id: String) async {
        reportQueue.cancel(peer: id)
        try? await send(id, report: 1, payload: HIDReportEncoder.keyboard(modifier: 0, usages: []))
        if let data = try? HIDReportEncoder.mouse() { try? await send(id, report: 2, payload: data) }
    }

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn else {
            advertisingPending = false
            if peripheral.state == .resetting || peripheral.state == .poweredOff {
                publishedService = nil; keyboard = nil; mouse = nil
                for id in peers.keys { reportQueue.cancel(peer: id, forget: true) }
                peers.removeAll(); subscriptions.removeAll()
            }
            let states: [CBManagerState: String] = [.unknown: "waitingForBluetooth", .resetting: "resetting",
                .unsupported: "peripheralUnsupported", .unauthorized: "bluetoothPermissionDenied", .poweredOff: "bluetoothOff"]
            state = states[peripheral.state] ?? "unknown"
            switch peripheral.state {
            case .unsupported:
                lastError = "This host/OS execution context reports no Bluetooth LE peripheral support. Check the adapter/driver and run in a host context that exposes Bluetooth; HID cannot be emulated by the web server."
            case .unauthorized:
                lastError = "Allow Open iPhone Use Headless in System Settings > Privacy & Security > Bluetooth."
            case .poweredOff:
                lastError = "Enable Bluetooth on the host, then reconnect the phone."
            default:
                lastError = "Waiting for the host Bluetooth service; approve the OS permission prompt if shown."
            }
            for id in peers.keys { reportQueue.cancel(peer: id, forget: true) }
            peers.removeAll(); subscriptions.removeAll()
            return
        }
        lastError = nil
        if publishedService != nil { resumeAdvertising(peripheral); return }
        func encryptedRead(_ uuid: String) -> CBMutableCharacteristic {
            CBMutableCharacteristic(type: CBUUID(string: uuid), properties: [.read], value: nil, permissions: [.readEncryptionRequired])
        }
        func report(_ id: UInt8) -> CBMutableCharacteristic {
            let characteristic = CBMutableCharacteristic(type: CBUUID(string: "2A4D"), properties: [.read, .notifyEncryptionRequired],
                                                         value: nil, permissions: [.readEncryptionRequired])
            characteristic.descriptors = [CBMutableDescriptor(type: CBUUID(string: "2908"), value: Data([id, 1]))]
            return characteristic
        }
        keyboard = report(1); mouse = report(2)
        let control = CBMutableCharacteristic(type: CBUUID(string: "2A4C"), properties: [.writeWithoutResponse], value: nil, permissions: [.writeEncryptionRequired])
        let mode = CBMutableCharacteristic(type: CBUUID(string: "2A4E"), properties: [.read, .writeWithoutResponse], value: nil, permissions: [.readEncryptionRequired, .writeEncryptionRequired])
        let service = CBMutableService(type: hid, primary: true)
        service.characteristics = [encryptedRead("2A4A"), encryptedRead("2A4B"), control, mode, keyboard!, mouse!]
        if let error = OpenIPhoneUseTryAddService(peripheral, service) { state = "servicePublishFailed"; lastError = error }
        else { publishedService = service; state = "publishing" }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error { publishedService = nil; state = "servicePublishFailed"; lastError = error.localizedDescription; return }
        state = "ready"
        resumeAdvertising(peripheral)
    }

    private func resumeAdvertising(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn, publishedService != nil,
              state != "publishing", !advertisingPending else { return }
        if peripheral.isAdvertising { state = "advertising"; return }
        advertisingPending = true
        peripheral.startAdvertising([CBAdvertisementDataLocalNameKey: "Open iPhone Use", CBAdvertisementDataServiceUUIDsKey: [hid]])
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        advertisingPending = false
        state = error == nil ? "advertising" : "advertisingFailed"
        lastError = error?.localizedDescription
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, willRestoreState dict: [String: Any]) {
        guard let services = dict[CBPeripheralManagerRestoredStateServicesKey] as? [CBMutableService],
              let service = services.first(where: { $0.uuid == hid }),
              let characteristics = service.characteristics else { return }
        var reports: [Int: CBMutableCharacteristic] = [:]
        for case let characteristic as CBMutableCharacteristic in characteristics where characteristic.uuid == CBUUID(string: "2A4D") {
            guard let reference = characteristic.descriptors?.first(where: { $0.uuid == CBUUID(string: "2908") })?.value as? Data,
                  reference.count == 2, reference[1] == 1 else { continue }
            reports[Int(reference[0])] = characteristic
        }
        guard let restoredKeyboard = reports[1], let restoredMouse = reports[2] else {
            // An incomplete/old schema must be republished, never routed by guessed order.
            peripheral.removeAllServices()
            return
        }
        publishedService = service; keyboard = restoredKeyboard; mouse = restoredMouse
        for (report, characteristic) in reports {
            for central in characteristic.subscribedCentrals ?? [] {
                let id = central.identifier.uuidString
                peers[id] = central; subscriptions[id, default: []].insert(report)
            }
        }
        state = "ready"
        // poweredOn may be delivered after restoration; that callback resumes advertising.
        resumeAdvertising(peripheral)
    }

    private func reportID(_ characteristic: CBCharacteristic) -> Int? {
        characteristic === keyboard ? 1 : (characteristic === mouse ? 2 : nil)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        guard let report = reportID(characteristic) else { return }
        let id = central.identifier.uuidString
        peers[id] = central
        subscriptions[id, default: []].insert(report)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        guard let report = reportID(characteristic) else { return }
        let id = central.identifier.uuidString
        reportQueue.cancel(peer: id)
        subscriptions[id]?.remove(report)
        if subscriptions[id]?.isEmpty == true {
            peers.removeValue(forKey: id); subscriptions.removeValue(forKey: id)
            reportQueue.cancel(peer: id, forget: true)
        }
        resumeAdvertising(peripheral)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        let value: Data
        switch request.characteristic.uuid.uuidString {
        case "2A4A": value = Data([0x11, 1, 0, 2])
        case "2A4B": value = BLEHIDPeripheral.reportDescriptor
        case "2A4E": value = Data([1])
        case "2A4D": value = reportQueue.value(peer: request.central.identifier.uuidString, report: request.characteristic === keyboard ? 1 : 2)
        default: peripheral.respond(to: request, withResult: .attributeNotFound); return
        }
        guard request.offset >= 0 && request.offset <= value.count else { peripheral.respond(to: request, withResult: .invalidOffset); return }
        request.value = value.subdata(in: request.offset..<value.count)
        peripheral.respond(to: request, withResult: .success)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let first = requests.first else { return }
        // CoreBluetooth requires one response for the entire write batch.
        let valid = requests.allSatisfy { request in
            guard request.offset == 0, let value = request.value, value.count == 1 else { return false }
            switch request.characteristic.uuid.uuidString {
            case "2A4C": return value[0] == 0 || value[0] == 1
            case "2A4E": return value[0] == 1 // Report protocol only; no Boot reports published.
            default: return false
            }
        }
        peripheral.respond(to: first, withResult: valid ? .success : .requestNotSupported)
    }
}

private enum RadioError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let message) = self { return message }; return nil }
}
