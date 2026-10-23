import Foundation

/// A measured, bounded retry, never a random target jitter or a global calibration update.
public enum PointerRetry {
    public static func correction(targetX: Double, targetY: Double,
                                  observedX: Double, observedY: Double) -> (x: Double, y: Double)? {
        guard [targetX, targetY, observedX, observedY].allSatisfy({ $0.isFinite && (0...1).contains($0) }) else { return nil }
        let dx = targetX - observedX, dy = targetY - observedY
        guard max(abs(dx), abs(dy)) <= 0.08, max(abs(dx), abs(dy)) > 0.002 else { return nil }
        return (dx, dy)
    }
}
