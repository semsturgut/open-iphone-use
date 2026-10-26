import Foundation

/// A target is safe to click only after fresh visual feedback confirms convergence.
public enum PointerAim {
    public enum Failure: Error, LocalizedError {
        case unavailable, didNotConverge
        public var errorDescription: String? {
            switch self {
            case .unavailable: "The pointer could not be observed reliably. Keep the screen visible and try again; no click was sent."
            case .didNotConverge: "The pointer did not converge within 1% after three corrections. Recalibrate and check AssistiveTouch tracking speed and Zoom; no click was sent."
            }
        }
    }

    @MainActor
    public static func align(target: PointerLocator.Point, span: PointerCalibration.Span,
                             observe: () async throws -> PointerLocator.Point?,
                             move: (_ dx: Int, _ dy: Int) async throws -> Void) async throws {
        guard target.x.isFinite, target.y.isFinite, (0...1).contains(target.x), (0...1).contains(target.y) else { throw Failure.unavailable }
        for attempt in 0...3 {
            guard let found = try await observe(), found.x.isFinite, found.y.isFinite,
                  (0...1).contains(found.x), (0...1).contains(found.y),
                  hypot(target.x - found.x, target.y - found.y) <= 0.18 else { throw Failure.unavailable }
            let dx = target.x - found.x, dy = target.y - found.y
            if hypot(dx, dy) <= 0.01 { return }
            guard attempt < 3 else { throw Failure.didNotConverge }
            let ux = Int((dx * Double(span.width)).rounded()), uy = Int((dy * Double(span.height)).rounded())
            guard ux != 0 || uy != 0 else { throw Failure.didNotConverge }
            try await move(ux, uy)
        }
    }
}
