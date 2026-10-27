@preconcurrency import CoreBluetooth
import Foundation
import OpenIPhoneUseCore

/// Never correlate a CoreBluetooth UUID to an unrelated classic Bluetooth address/name.
/// Reverse-role retrieval is best effort: iOS need not expose a peripheral GAP service.
@MainActor
final class PeerNameResolver: NSObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    private var manager: CBCentralManager?
    private var wanted: Set<UUID> = []
    private var attempts: [UUID: (Date, Int)] = [:]
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var names: [UUID: (String, String)] = [:]
    func start() { manager = CBCentralManager(delegate: self, queue: .main) }
    func name(_ id: UUID) -> (String, String) {
        wanted.insert(id); resolve()
        return names[id] ?? ("iPhone", "fallback")
    }
    func stop() {
        for peripheral in peripherals.values { manager?.cancelPeripheralConnection(peripheral) }
        peripherals.removeAll(); manager = nil
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) { resolve() }
    private func resolve() {
        guard let manager, manager.state == .poweredOn else { return }
        for id in wanted {
            let previous = attempts[id] ?? (.distantPast, 0)
            guard peripherals[id] == nil, previous.1 < 3, Date().timeIntervalSince(previous.0) >= 15 else { continue }
            attempts[id] = (Date(), previous.1 + 1)
            guard let peripheral = manager.retrievePeripherals(withIdentifiers: [id]).first(where: { $0.identifier == id }) else { continue }
            if let name = BluetoothPeerName.specific(peripheral.name) { names[id] = (name, "corebluetooth-cache") }
            // Even a cached "iPhone" label must not short-circuit the GAP read.
            // The owner's full name can be available there while the cache is generic.
            peripherals[id] = peripheral; peripheral.delegate = self
            manager.connect(peripheral)
            Task { @MainActor [weak self, weak peripheral] in
                try? await Task.sleep(for: .seconds(4))
                guard let self, let peripheral, self.peripherals[id] != nil else { return }
                self.manager?.cancelPeripheralConnection(peripheral); self.peripherals.removeValue(forKey: id)
            }
        }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([CBUUID(string: "1800")])
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        peripherals.removeValue(forKey: peripheral.identifier)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil else { return }
        for service in peripheral.services ?? [] where service.uuid == CBUUID(string: "1800") {
            peripheral.discoverCharacteristics([CBUUID(string: "2A00")], for: service)
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil else { return }
        for characteristic in service.characteristics ?? [] where characteristic.uuid == CBUUID(string: "2A00") {
            peripheral.readValue(for: characteristic)
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil, characteristic.uuid == CBUUID(string: "2A00"),
              let data = characteristic.value, let name = String(data: data, encoding: .utf8), !name.isEmpty else { return }
        if let specific = BluetoothPeerName.specific(name) {
            names[peripheral.identifier] = (specific, "gap-device-name")
            attempts[peripheral.identifier] = (Date(), 3)
        }
        manager?.cancelPeripheralConnection(peripheral); peripherals.removeValue(forKey: peripheral.identifier)
    }
}
