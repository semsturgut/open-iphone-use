import Foundation

/// Learned mapping between BLE HID mouse units and iPhone screen fractions.
///
/// A relative pointer has no idea where it is, so the app homes it into the top-left
/// corner and counts units from there. iOS applies its own pointer acceleration, so the
/// units needed to cross the screen are measured rather than assumed, and every observed
/// landing refines a per-axis line fit: `landed = a * aimed + b`.
public struct PointerCalibration: Codable, Equatable, Sendable {
    public enum Axis: String, Codable, Sendable, CaseIterable {
        case x, y
    }

    /// Mouse units needed to cross the whole screen width and height.
    public struct Span: Codable, Equatable, Sendable {
        public var width: Int
        public var height: Int

        public init(width: Int, height: Int) {
            self.width = width
            self.height = height
        }
    }

    /// One observed move: the fraction aimed at and the fraction it actually landed on.
    public struct Sample: Codable, Equatable, Sendable {
        public var aimed: Double
        public var landed: Double

        public init(aimed: Double, landed: Double) {
            self.aimed = aimed
            self.landed = landed
        }
    }

    // Version 2 stores screen fractions. Old viewport-based calibrations must be remeasured.
    public private(set) var coordinateSpaceVersion = 2
    public private(set) var deviceIdentifier: String?
    public mutating func bindDevice(_ identifier: String?) { deviceIdentifier = identifier }
    public private(set) var screenAspectRatio: Double?
    public mutating func setScreenAspectRatio(_ value: Double) { screenAspectRatio = value }

    /// Invalidates calibration measured with an earlier transport cadence.
    public static let currentMotionVersion = 7
    public private(set) var motionVersion: Int?
    public enum Evidence: String, Codable, Sendable { case twoPointEstimate, curveEstimate, heldOut }
    public private(set) var evidence: Evidence?
    public mutating func setEvidence(_ value: Evidence) { evidence = value }

    public private(set) var motionProfile: PointerMotion.Profile?
    public mutating func setMotionProfile(_ profile: PointerMotion.Profile) { motionProfile = profile; motionVersion = Self.currentMotionVersion }

    public static let maximumSamples = 24
    /// A miss larger than this is corrected but never learned from: it is usually a stray reading.
    public static let maximumLearnedMiss = 0.06
    /// Corrections outside this gain are treated as noise and ignored.
    public static let gainRange = 0.85...1.15

    public private(set) var span: Span?
    public private(set) var quality: Double?
    public private(set) var samples: [Axis: [Sample]]

    public init(span: Span? = nil, quality: Double? = nil, samples: [Axis: [Sample]] = [:]) {
        self.span = span
        self.quality = quality
        self.samples = samples
    }

    public var isCalibrated: Bool { span != nil && coordinateSpaceVersion == 2 }

    /// Reuse only a board-validated model measured for the current physical screen.
    public func canReuse(forScreenAspect aspect: Double?, deviceIdentifier currentDevice: String?) -> Bool {
        guard let currentDevice, !currentDevice.isEmpty, deviceIdentifier == currentDevice,
              motionProfile != nil, motionVersion == Self.currentMotionVersion,
              isCalibrated, let evidence,
              let saved = screenAspectRatio, saved.isFinite, saved > 0,
              let aspect, aspect.isFinite, aspect > 0 else { return false }
        switch evidence {
        case .heldOut:
            guard let quality, quality.isFinite, (0...0.025).contains(quality) else { return false }
        case .twoPointEstimate, .curveEstimate:
            // No fictitious zero RMS: two points fit a line exactly and do not validate it.
            guard quality == nil, let span,
                  (32...20_000).contains(span.width), (32...20_000).contains(span.height),
                  Axis.allCases.allSatisfy({ samples(for: $0).count >= 2 }) else { return false }
        }
        return abs(saved - aspect) < 0.01
    }

    public func samples(for axis: Axis) -> [Sample] { samples[axis] ?? [] }

    public mutating func apply(span: Span, quality: Double? = nil) {
        self.span = span
        self.quality = quality
        samples = [:]
    }

    public mutating func setQuality(_ value: Double?) { quality = value }

    public mutating func remember(_ axis: Axis, aimed: Double, landed: Double) {
        var list = samples[axis] ?? []
        list.append(Sample(aimed: aimed, landed: landed))
        samples[axis] = Array(list.suffix(Self.maximumSamples))
    }

    /// Least-squares line through the observed landings for one axis.
    public static func fit(_ points: [Sample]) -> (a: Double, b: Double, rms: Double)? {
        guard points.count >= 2 else { return nil }
        let n = Double(points.count)
        let meanAimed = points.map(\.aimed).reduce(0, +) / n
        let meanLanded = points.map(\.landed).reduce(0, +) / n
        let variance = points.map { ($0.aimed - meanAimed) * ($0.aimed - meanAimed) }.reduce(0, +)
        guard variance > 1e-6 else { return nil }
        let a = points.map { ($0.aimed - meanAimed) * ($0.landed - meanLanded) }.reduce(0, +) / variance
        guard a > 0 else { return nil }
        let b = meanLanded - (a * meanAimed)
        let rms = (points.map { let e = $0.landed - ((a * $0.aimed) + b); return e * e }.reduce(0, +) / n).squareRoot()
        return (a, b, rms)
    }

    /// The fraction to aim at so the pointer lands on `target`, using the learned line.
    public func corrected(_ target: Double, axis: Axis) -> Double {
        let points = samples(for: axis)
        guard points.count >= 2 else { return target }
        // Fast iOS motion is nonlinear during its initial acceleration. Invert the
        // measured monotone curve rather than extrapolating one line to the bottom edge.
        let sorted = points.sorted { $0.aimed < $1.aimed }
        var unique: [Sample] = []
        for point in sorted {
            if let last = unique.last, abs(last.aimed - point.aimed) < 0.001 {
                unique[unique.count - 1] = Sample(aimed: last.aimed, landed: (last.landed + point.landed) / 2)
            } else { unique.append(point) }
        }
        if unique.count >= 3,
           zip(unique, unique.dropFirst()).allSatisfy({ $1.landed - $0.landed > 0.005 }) {
            if unique[0].aimed > 0, unique[0].landed > 0 {
                unique.insert(Sample(aimed: 0, landed: 0), at: 0)
            }
            let upper = unique.firstIndex { $0.landed >= target } ?? unique.count - 1
            let index = max(1, upper)
            let a = unique[index - 1], b = unique[index]
            return a.aimed + (target - a.landed) * (b.aimed - a.aimed) / (b.landed - a.landed)
        }
        let n = Double(points.count)
        let meanAimed = points.map(\.aimed).reduce(0, +) / n
        let meanLanded = points.map(\.landed).reduce(0, +) / n
        let variance = points.map { ($0.aimed - meanAimed) * ($0.aimed - meanAimed) }.reduce(0, +)
        var a = variance > 1e-4
            ? points.map { ($0.aimed - meanAimed) * ($0.landed - meanLanded) }.reduce(0, +) / variance
            : 1
        a = Self.gainRange.contains(a) ? a : 1
        let b = meanLanded - (a * meanAimed)
        guard abs(b) < 0.1 else { return target }
        return (target - b) / a
    }

    /// How far the learned correction moves a target, as a fraction of the screen.
    public var drift: Double {
        [0.2, 0.8].flatMap { target in
            Axis.allCases.map { abs(corrected(target, axis: $0) - target) }
        }.max() ?? 0
    }

    /// Units from home that should land on a screen fraction, or nil when uncalibrated.
    public func units(for fraction: Double, axis: Axis) -> Int? {
        guard let span else { return nil }
        // The learned model is in physical screen fractions, independent of Safari.
        // Bounded slack remains available for diagnostic offsets; public tap/move tools
        // accept only targets in 0...1.
        let usable = min(max(fraction, -Self.fractionSlack), 1 + Self.fractionSlack)
        let extent = axis == .x ? span.width : span.height
        return Int((corrected(usable, axis: axis) * Double(extent)).rounded())
    }

    /// Bounded extra range for internal calibration diagnostics, not a viewport transform.
    public static let fractionSlack = 0.35

    /// The screen fraction a unit offset from home should land on.
    public func fraction(forUnits units: Int, axis: Axis) -> Double? {
        guard let span else { return nil }
        let extent = axis == .x ? span.width : span.height
        guard extent > 0 else { return nil }
        return Double(units) / Double(extent)
    }

    public struct Observation: Sendable {
        public let unitsX: Int, unitsY: Int
        public let x: Double, y: Double
        public init(unitsX: Int, unitsY: Int, x: Double, y: Double) {
            self.unitsX = unitsX; self.unitsY = unitsY; self.x = x; self.y = y
        }
    }

    /// Wide baselines limit extrapolation error. Edge-clamped or reversed samples fail closed.
    public static func twoPointEstimate(_ first: Observation, _ second: Observation) -> Self? {
        let points = [first, second]
        guard points.allSatisfy({ $0.x.isFinite && $0.y.isFinite &&
            (0.025...0.95).contains($0.x) && (0.025...0.95).contains($0.y) }),
              second.x - first.x >= 0.25, second.y - first.y >= 0.25,
              second.unitsX > first.unitsX, second.unitsY > first.unitsY else { return nil }
        let width = Int((Double(second.unitsX - first.unitsX) / (second.x - first.x)).rounded())
        let height = Int((Double(second.unitsY - first.unitsY) / (second.y - first.y)).rounded())
        guard (32...20_000).contains(width), (32...20_000).contains(height) else { return nil }
        var model = Self(span: .init(width: width, height: height))
        for point in points {
            model.remember(.x, aimed: Double(point.unitsX) / Double(width), landed: point.x)
            model.remember(.y, aimed: Double(point.unitsY) / Double(height), landed: point.y)
        }
        guard Axis.allCases.allSatisfy({ axis in
            guard let fit = fit(model.samples(for: axis)) else { return false }
            return abs(fit.b) < 0.1
        }) else { return nil }
        model.setEvidence(.twoPointEstimate)
        return model
    }

    /// Span from one measured landing: `units` moved from home landed on `fraction`.
    public static func span(fromUnits units: Int, landedAt fraction: Double) -> Span? {
        guard units > 0, fraction > 0.02, fraction < 0.98 else { return nil }
        let extent = Int((Double(units) / fraction).rounded())
        guard extent > 0 else { return nil }
        return Span(width: extent, height: extent)
    }

    /// Span from two landings at two known unit offsets, per axis.
    public static func span(first: (units: Int, x: Double, y: Double),
                            second: (units: Int, x: Double, y: Double)) -> Span? {
        let deltaUnits = Double(second.units - first.units)
        let deltaX = second.x - first.x
        let deltaY = second.y - first.y
        guard deltaUnits > 0, deltaX > 0.02, deltaY > 0.02 else { return nil }
        let width = Int((deltaUnits / deltaX).rounded())
        let height = Int((deltaUnits / deltaY).rounded())
        guard width > 0, height > 0 else { return nil }
        return Span(width: width, height: height)
    }
}
