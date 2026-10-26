@preconcurrency import CoreBluetooth
import Foundation
import OpenIPhoneUseCore
import ObjCExceptionCatcher

/// One published HID service, many subscribed centrals. Every send names exactly one peer.
/// The legacy single-phone peripheral/controller remain unchanged for the optional app.
@MainActor
final class HeadlessRadio: NSObject, @preconcurrency CBPeripheralManagerDelegate {
    private var manager: CBPeripheralManager?
    private var keyboard: CBMutableCharacteristic?
    private var mouse: CBMutableCharacteristic?
    private var peers: [String: CBCentral] = [:]
    private var subscriptions: [String: Set<Int>] = [:]
    private var generations: [String: Int] = [:]
    private var broker: UnixSocketBroker?
    private var state = "starting"
    private var lastError: String?
    private let hid = CBUUID(string: "1812")

    func start() throws {
        broker = UnixSocketBroker { [weak self] request in
            guard let self else { return BrokerResponse(requestID: request.requestID, ok: false, message: "Radio stopped") }
            return await self.handle(request)
        }
        try broker?.start()
        if ProcessInfo.processInfo.environment["OIU_DISABLE_BLUETOOTH"] == "1" { state = "bluetoothDisabledForTest"; return }
        manager = CBPeripheralManager(delegate: self, queue: .main,
                                      options: [CBPeripheralManagerOptionShowPowerAlertKey: true])
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.state == "starting" else { return }
            self.state = CBManager.authorization == .notDetermined ? "waitingForBluetoothPermission" : "bluetoothInitializationTimedOut"
            self.lastError = "CoreBluetooth has not initialized. Approve the host Bluetooth prompt, enable Bluetooth, and restart outside a restricted sandbox if its XPC service is unavailable."
        }
    }

    func stop() async {
        for id in Array(peers.keys) { await release(id) }
        manager?.stopAdvertising()
        manager?.removeAllServices()
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
                    ["id": $0, "reports": Array(subscriptions[$0] ?? []).sorted()] as [String: Any]
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
        guard let manager, let peer = peers[id], subscriptions[id]?.contains(report) == true,
              let characteristic = report == 1 ? keyboard : mouse else {
            throw RadioError.message("iPhone disconnected or not subscribed to both HID reports; pair and enable AssistiveTouch")
        }
        let generation = generations[id, default: 0]
        let deadline = Date().addingTimeInterval(3)
        while true {
            try Task.checkCancellation()
            guard generations[id, default: 0] == generation, peers[id] != nil else {
                throw RadioError.message("HID send canceled or peer disconnected")
            }
            // Passing a concrete central array is essential: nil broadcasts to all phones.
            if manager.updateValue(payload, for: characteristic, onSubscribedCentrals: [peer]) { return }
            guard Date() < deadline else { throw RadioError.message("Bluetooth report backpressure timeout") }
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    private func release(_ id: String) async {
        generations[id, default: 0] += 1
        try? await send(id, report: 1, payload: HIDReportEncoder.keyboard(modifier: 0, usages: []))
        if let data = try? HIDReportEncoder.mouse() { try? await send(id, report: 2, payload: data) }
    }

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn else {
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
            for id in peers.keys { generations[id, default: 0] += 1 }
            peers.removeAll(); subscriptions.removeAll()
            return
        }
        lastError = nil
        func constant(_ uuid: String, _ value: Data) -> CBMutableCharacteristic {
            CBMutableCharacteristic(type: CBUUID(string: uuid), properties: [.read], value: value, permissions: [.readEncryptionRequired])
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
        service.characteristics = [constant("2A4A", Data([0x11, 1, 0, 2])),
                                   constant("2A4B", BLEHIDPeripheral.reportDescriptor), control, mode, keyboard!, mouse!]
        if let error = OpenIPhoneUseTryAddService(peripheral, service) { state = "servicePublishFailed"; lastError = error }
        else { state = "publishing" }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error { state = "servicePublishFailed"; lastError = error.localizedDescription; return }
        peripheral.startAdvertising([CBAdvertisementDataLocalNameKey: "Open iPhone Use", CBAdvertisementDataServiceUUIDsKey: [hid]])
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        state = error == nil ? "advertising" : "advertisingFailed"
        lastError = error?.localizedDescription
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
        generations[id, default: 0] += 1
        subscriptions[id]?.remove(report)
        if subscriptions[id]?.isEmpty == true { peers.removeValue(forKey: id); subscriptions.removeValue(forKey: id) }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        let value: Data
        switch request.characteristic.uuid.uuidString {
        case "2A4E": value = Data([1])
        case "2A4D": value = request.characteristic === keyboard ? Data(repeating: 0, count: 8) : Data(repeating: 0, count: 4)
        default: peripheral.respond(to: request, withResult: .attributeNotFound); return
        }
        guard request.offset <= value.count else { peripheral.respond(to: request, withResult: .invalidOffset); return }
        request.value = value.subdata(in: request.offset..<value.count)
        peripheral.respond(to: request, withResult: .success)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        for request in requests {
            let valid = ["2A4C", "2A4E"].contains(request.characteristic.uuid.uuidString) && request.value?.count == 1
            peripheral.respond(to: request, withResult: valid ? .success : .requestNotSupported)
        }
    }
}

private enum RadioError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let message) = self { return message }; return nil }
}
