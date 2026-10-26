import Foundation

public enum BluetoothPeerName {
    /// A generic model label is not the owner's Bluetooth name.
    public static func specific(_ value: String?) -> String? {
        guard let value else { return nil }
        let clean = String(String.UnicodeScalarView(value.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, !["iphone", "apple iphone", "unknown", "bluetooth device"].contains(clean.lowercased()) else { return nil }
        return String(clean.prefix(80))
    }
}
