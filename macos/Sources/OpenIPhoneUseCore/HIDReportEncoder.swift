import Foundation

public enum HIDReportError: Error, Equatable {
    case unsupportedCharacter(Character)
    case invalidKey(String)
    case invalidButton(String)
    case textTooLong
    case movementOutOfRange
}

public struct KeyboardStroke: Sendable, Equatable {
    public let modifier: UInt8
    public let usage: UInt8

    public init(modifier: UInt8, usage: UInt8) {
        self.modifier = modifier
        self.usage = usage
    }
}

public enum HIDReportEncoder {
    // HOGP carries the report ID in the Report Reference descriptor, not in
    // the characteristic value. Payloads below contain only report fields.
    public static let keyboardReportID: UInt8 = 1
    public static let mouseReportID: UInt8 = 2

    public static func keyboard(modifier: UInt8, usages: [UInt8]) -> Data {
        var report = Data([modifier, 0])
        report.append(contentsOf: Array(usages.prefix(6)))
        if usages.count < 6 { report.append(contentsOf: repeatElement(0, count: 6 - usages.count)) }
        return report
    }

    public static func mouse(buttons: UInt8 = 0, dx: Int = 0, dy: Int = 0, wheel: Int = 0) throws -> Data {
        guard (-127...127).contains(dx), (-127...127).contains(dy), (-127...127).contains(wheel) else {
            throw HIDReportError.movementOutOfRange
        }
        return Data([buttons, UInt8(bitPattern: Int8(dx)), UInt8(bitPattern: Int8(dy)), UInt8(bitPattern: Int8(wheel))])
    }

    public static func buttonMask(_ button: String) throws -> UInt8 {
        switch button.lowercased() {
        case "left": return 0x01
        case "right": return 0x02
        case "middle": return 0x04
        default: throw HIDReportError.invalidButton(button)
        }
    }

    public static func text(_ text: String) throws -> [KeyboardStroke] {
        guard text.utf8.count <= 500 else { throw HIDReportError.textTooLong }
        return try text.map(stroke(for:))
    }

    public static func stroke(for character: Character) throws -> KeyboardStroke {
        guard character.unicodeScalars.count == 1,
              let scalar = character.unicodeScalars.first,
              scalar.isASCII else { throw HIDReportError.unsupportedCharacter(character) }

        let value = scalar.value
        switch value {
        case 97...122: return KeyboardStroke(modifier: 0, usage: UInt8(value - 93)) // a-z => 0x04...0x1D
        case 65...90: return KeyboardStroke(modifier: 0x02, usage: UInt8(value - 61))
        case 49...57: return KeyboardStroke(modifier: 0, usage: UInt8(value - 19)) // 1-9 => 0x1E...0x26
        case 48: return KeyboardStroke(modifier: 0, usage: 0x27)
        case 32: return KeyboardStroke(modifier: 0, usage: 0x2C)
        case 10, 13: return KeyboardStroke(modifier: 0, usage: 0x28)
        case 9: return KeyboardStroke(modifier: 0, usage: 0x2B)
        case 8: return KeyboardStroke(modifier: 0, usage: 0x2A)
        default:
            let map: [UInt32: KeyboardStroke] = [
                33: .init(modifier: 0x02, usage: 0x1E), 34: .init(modifier: 0x02, usage: 0x34),
                35: .init(modifier: 0x02, usage: 0x20), 36: .init(modifier: 0x02, usage: 0x21),
                37: .init(modifier: 0x02, usage: 0x22), 38: .init(modifier: 0x02, usage: 0x24),
                39: .init(modifier: 0, usage: 0x34), 40: .init(modifier: 0x02, usage: 0x26),
                41: .init(modifier: 0x02, usage: 0x27), 42: .init(modifier: 0x02, usage: 0x25),
                43: .init(modifier: 0x02, usage: 0x2E), 44: .init(modifier: 0, usage: 0x36),
                45: .init(modifier: 0, usage: 0x2D), 46: .init(modifier: 0, usage: 0x37),
                47: .init(modifier: 0, usage: 0x38), 58: .init(modifier: 0x02, usage: 0x33),
                59: .init(modifier: 0, usage: 0x33), 60: .init(modifier: 0x02, usage: 0x36),
                61: .init(modifier: 0, usage: 0x2E), 62: .init(modifier: 0x02, usage: 0x37),
                63: .init(modifier: 0x02, usage: 0x38), 64: .init(modifier: 0x02, usage: 0x1F),
                91: .init(modifier: 0, usage: 0x2F), 92: .init(modifier: 0, usage: 0x31),
                93: .init(modifier: 0, usage: 0x30), 94: .init(modifier: 0x02, usage: 0x23),
                95: .init(modifier: 0x02, usage: 0x2D), 96: .init(modifier: 0, usage: 0x35),
                123: .init(modifier: 0x02, usage: 0x2F), 124: .init(modifier: 0x02, usage: 0x31),
                125: .init(modifier: 0x02, usage: 0x30), 126: .init(modifier: 0x02, usage: 0x35),
            ]
            guard let stroke = map[value] else { throw HIDReportError.unsupportedCharacter(character) }
            return stroke
        }
    }

    /// Key combinations such as `cmd+h` or `cmd+space`, which iOS uses for system shortcuts.
    ///
    /// Plain keys go through `specialKey`; a `+` makes it a chord with held modifiers.
    public static func chord(_ combination: String) throws -> KeyboardStroke {
        let parts = combination.lowercased().split(separator: "+").map(String.init)
        guard parts.count >= 2 else { return try specialKey(combination) }
        var modifier: UInt8 = 0
        for name in parts.dropLast() {
            switch name {
            case "cmd", "command", "gui", "super": modifier |= 0x08
            case "shift": modifier |= 0x02
            case "alt", "option", "opt": modifier |= 0x04
            case "ctrl", "control": modifier |= 0x01
            default: throw HIDReportError.invalidKey(combination)
            }
        }
        guard let last = parts.last else { throw HIDReportError.invalidKey(combination) }
        // Single letters and digits keep their normal key positions inside a chord.
        let base: KeyboardStroke
        if last.count == 1, let character = last.first {
            base = try stroke(for: character)
        } else if last == "space" {
            base = KeyboardStroke(modifier: 0, usage: 0x2C)
        } else {
            base = try specialKey(last)
        }
        return KeyboardStroke(modifier: modifier | base.modifier, usage: base.usage)
    }

    public static func specialKey(_ key: String) throws -> KeyboardStroke {
        let entries: [String: KeyboardStroke] = [
            "enter": .init(modifier: 0, usage: 0x28), "tab": .init(modifier: 0, usage: 0x2B),
            "escape": .init(modifier: 0, usage: 0x29), "backspace": .init(modifier: 0, usage: 0x2A),
            "delete": .init(modifier: 0, usage: 0x4C), "space": .init(modifier: 0, usage: 0x2C),
            "up": .init(modifier: 0, usage: 0x52), "down": .init(modifier: 0, usage: 0x51),
            "left": .init(modifier: 0, usage: 0x50), "right": .init(modifier: 0, usage: 0x4F),
            "home": .init(modifier: 0, usage: 0x4A), "end": .init(modifier: 0, usage: 0x4D),
            "pageup": .init(modifier: 0, usage: 0x4B), "pagedown": .init(modifier: 0, usage: 0x4E),
        ]
        guard let stroke = entries[key.lowercased()] else { throw HIDReportError.invalidKey(key) }
        return stroke
    }
}
