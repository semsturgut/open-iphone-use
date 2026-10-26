@preconcurrency import CoreBluetooth
import Foundation

/// Never correlate a CoreBluetooth UUID to an unrelated classic Bluetooth address/name.
/// Reverse-role retrieval is best effort: iOS need not expose a peripheral GAP service.
@MainActor
final class PeerNameResolver: NSObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    private var manager: CBCentralManager?
    private var wanted: Set<UUID> = []
    private var attempted: Set<UUID> = []
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var names: [UUID: (String, String)] = [:]
    func start() { manager = CBCentralManager(delegate: self, queue: .main) }
    func name(_ id: UUID) -> (String, String) {
        wanted.insert(id); resolve()
        return names[id] ?? ("iPhone (\(id.uuidString.suffix(4).lowercased()))", "fallback")
    }
    func stop() {
        for peripheral in peripherals.values { manager?.cancelPeripheralConnection(peripheral) }
        peripherals.removeAll(); manager = nil
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) { resolve() }
    private func resolve() {
        guard let manager, manager.state == .poweredOn else { return }
        for id in wanted.subtracting(attempted) {
            attempted.insert(id)
            guard let peripheral = manager.retrievePeripherals(withIdentifiers: [id]).first(where: { $0.identifier == id }) else { continue }
            if let name = peripheral.name, !name.isEmpty { names[id] = (String(name.prefix(80)), "corebluetooth-cache"); continue }
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
        names[peripheral.identifier] = (String(name.prefix(80)), "gap-device-name")
        manager?.cancelPeripheralConnection(peripheral); peripherals.removeValue(forKey: peripheral.identifier)
    }
}
