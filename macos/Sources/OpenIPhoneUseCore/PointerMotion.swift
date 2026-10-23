import Foundation

/// Plans BLE HID pointer motion as many small relative deltas instead of one jump.
///
/// iOS AssistiveTouch applies pointer acceleration to each report, so a single large
/// delta is both mis-scaled and visibly stepped. Splitting the same total distance into
/// distance-scaled steps with an ease-in/ease-out profile approximates a hardware mouse.
public enum PointerMotion {
    public static let maximumDelta = 127
    public static let minimumIntervalMilliseconds = 8
    public static let maximumIntervalMilliseconds = 60

    /// Fastest first. Each profile needs its own physical calibration because iOS
    /// acceleration depends on report size/cadence. An 8-ms minimum prevents
    /// all axis/button updates from collapsing into the same unpaced burst.
    public enum Profile: String, Codable, Sendable, CaseIterable {
        case burst127, turbo32, fast16, balanced8, precise4

        public var reportUnits: Int {
            switch self { case .burst127: 127; case .turbo32: 32; case .fast16: 16; case .balanced8: 8; case .precise4: 4 }
        }
        public var intervalMilliseconds: Int { self == .precise4 ? 12 : 8 }
        public var probeStops: [Int] { self == .precise4 ? [150, 350] : [64, 160] }
    }

    public struct Delta: Sendable, Equatable {
        public let dx: Int
        public let dy: Int

        public init(dx: Int, dy: Int) {
            self.dx = dx
            self.dy = dy
        }
    }

    public struct Plan: Sendable, Equatable {
        public let deltas: [Delta]
        public let intervalMilliseconds: Int

        public init(deltas: [Delta], intervalMilliseconds: Int) {
            self.deltas = deltas
            self.intervalMilliseconds = intervalMilliseconds
        }
    }

    /// Distance-scaled step count, mirroring ~4 units per step within sane bounds.
    public static func stepCount(forDistance distance: Double) -> Int {
        guard distance > 0 else { return 0 }
        return min(max(Int((distance / 4).rounded(.up)), 4), 48)
    }

    /// Smoothstep eases in and out, so the first and last steps are the smallest.
    private static func ease(_ t: Double) -> Double {
        let clamped = min(max(t, 0), 1)
        return clamped * clamped * (3 - (2 * clamped))
    }

    /// Splits one requested delta into paced sub-deltas whose sum is exactly the request.
    ///
    /// The total is preserved across the whole plan, so repeated calls accumulate no drift.
    /// Any step that would still exceed the HID limit is spread over extra steps.
    public static func plan(dx: Int, dy: Int, steps: Int? = nil, intervalMilliseconds: Int? = nil) -> Plan {
        let dx = min(max(dx, -maximumDelta), maximumDelta)
        let dy = min(max(dy, -maximumDelta), maximumDelta)
        let distance = (Double(dx) * Double(dx) + Double(dy) * Double(dy)).squareRoot()
        guard distance > 0 else { return Plan(deltas: [], intervalMilliseconds: minimumIntervalMilliseconds) }

        var count = max(steps ?? stepCount(forDistance: distance), 1)
        let largestAxis = max(abs(dx), abs(dy))
        if largestAxis / count > maximumDelta {
            count = Int((Double(largestAxis) / Double(maximumDelta)).rounded(.up))
        }

        let weights = (1...count).map { ease(Double($0) / Double(count)) }
        let totalWeight = weights.reduce(0, +)

        var deltas: [Delta] = []
        deltas.reserveCapacity(count)
        var previousX = 0
        var previousY = 0
        var cumulativeWeight = 0.0

        for weight in weights {
            cumulativeWeight += weight
            let targetX = Int((Double(dx) * cumulativeWeight / totalWeight).rounded())
            let targetY = Int((Double(dy) * cumulativeWeight / totalWeight).rounded())
            deltas.append(Delta(dx: targetX - previousX, dy: targetY - previousY))
            previousX = targetX
            previousY = targetY
        }

        // Rounding can leave a remainder; deliver it on the final step.
        if let last = deltas.indices.last, previousX != dx || previousY != dy {
            deltas[last] = Delta(dx: deltas[last].dx + (dx - previousX),
                                 dy: deltas[last].dy + (dy - previousY))
        }

        let interval = min(max(intervalMilliseconds ?? defaultInterval(forDistance: distance),
                               minimumIntervalMilliseconds),
                           maximumIntervalMilliseconds)
        return Plan(deltas: deltas.filter { $0.dx != 0 || $0.dy != 0 }, intervalMilliseconds: interval)
    }

    /// Calibration and absolute moves use the same report size, cadence and axis order.
    /// Easing changes iOS acceleration with distance; it cannot share one linear calibration.
    public static func calibratedPlan(dx: Int, dy: Int, profile: Profile = .turbo32) -> Plan {
        var deltas: [Delta] = []
        func appendAxis(_ total: Int, horizontal: Bool) {
            var remaining = total
            while remaining != 0 {
                let amount = max(-profile.reportUnits, min(profile.reportUnits, remaining))
                deltas.append(Delta(dx: horizontal ? amount : 0, dy: horizontal ? 0 : amount))
                remaining -= amount
            }
        }
        // Leave the top status edge vertically before travelling horizontally.
        appendAxis(dy, horizontal: false)
        appendAxis(dx, horizontal: true)
        return Plan(deltas: deltas, intervalMilliseconds: profile.intervalMilliseconds)
    }

    /// Larger jumps move slightly faster per step, like a quick flick of a real mouse.
    private static func defaultInterval(forDistance distance: Double) -> Int {
        if distance < 40 { return 24 }
        if distance < 90 { return 16 }
        return 12
    }
}
