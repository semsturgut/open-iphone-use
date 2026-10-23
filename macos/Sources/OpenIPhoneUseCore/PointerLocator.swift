import CoreGraphics
import Foundation
import ImageIO

/// Finds the iPhone pointer in a pair of frames by wiggling it and diffing the pixels.
///
/// A relative pointer is invisible to the transport, so the caller nudges it a known
/// number of units and takes a frame before and after. Only the pointer moves, so the
/// changed blob is the pointer. This is what makes closed-loop aiming possible without
/// asking a vision model where the cursor is.
public enum PointerLocator {
    public struct Point: Equatable, Sendable {
        public let x: Double
        public let y: Double

        public init(x: Double, y: Double) {
            self.x = x
            self.y = y
        }
    }

    public struct Search: Sendable {
        /// Centre of the search window, as a screen fraction.
        public var near: Point
        /// Half-size of the search window, as a screen fraction.
        public var reach: Point

        public init(near: Point = Point(x: 0.5, y: 0.5), reach: Point = Point(x: 0.12, y: 0.08)) {
            self.near = near
            self.reach = reach
        }
    }

    public static let pixelThreshold = 30
    public static let minimumHits = 15
    /// The status bar changes on its own; never look for the pointer there.
    public static let topInset = 0.05

    /// Locates the pointer, correcting for the wiggle that produced the two frames.
    ///
    /// `shift` is the wiggle distance as a screen fraction: the blob sits at the midpoint
    /// of the movement, so half of it is subtracted to report where the pointer started.
    public static func locate(before: Data, after: Data, search: Search = Search(), shift: Double = 0) -> Point? {
        guard let a = gray(before), let b = gray(after), a.width == b.width, a.height == b.height else { return nil }
        let width = a.width, height = a.height
        let left = max(0, Int((search.near.x - search.reach.x) * Double(width)))
        let top = max(Int(topInset * Double(height)), Int((search.near.y - search.reach.y) * Double(height)))
        let right = min(width, Int((search.near.x + search.reach.x) * Double(width)))
        let bottom = min(height, Int((search.near.y + search.reach.y) * Double(height)))
        guard right > left, bottom > top else { return nil }

        var sumX = 0, sumY = 0, hits = 0
        var minX = Int.max, maxX = Int.min, minY = Int.max, maxY = Int.min
        for y in top..<bottom {
            for x in left..<right {
                let index = (y * width) + x
                let delta = abs(Int(a.pixels[index]) - Int(b.pixels[index]))
                guard delta > pixelThreshold else { continue }
                hits += 1
                sumX += x
                sumY += y
                minX = min(minX, x); maxX = max(maxX, x)
                minY = min(minY, y); maxY = max(maxY, y)
            }
        }

        let area = (right - left) * (bottom - top)
        // Nothing moved, or far more than a pointer did.
        guard hits >= minimumHits, hits <= area / 4 else { return nil }
        // One compact spot, not a whole region of the screen.
        guard Double(maxX - minX) <= 0.15 * Double(width), Double(maxY - minY) <= 0.07 * Double(height) else { return nil }

        let centreX = (Double(sumX) / Double(hits)) / Double(width)
        let centreY = (Double(sumY) / Double(hits)) / Double(height)
        return Point(x: centreX - (shift / 2), y: centreY)
    }

    /// Rejects readings stuck against an edge, where a clamped pointer is useless for fitting.
    public static func usable(_ point: Point) -> Bool {
        point.x > 0.02 && point.x < 0.98 && point.y > 0.02 && point.y < 0.98
    }

    struct Gray {
        let pixels: [UInt8]
        let width: Int
        let height: Int
    }

    /// Decodes a JPEG into an 8-bit grayscale buffer whose row 0 is the top of the image.
    static func gray(_ data: Data) -> Gray? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        let width = image.width, height = image.height
        guard width > 0, height > 0 else { return nil }
        guard let space = CGColorSpace(name: CGColorSpace.genericGrayGamma2_2) ?? CGColorSpace(name: CGColorSpace.linearGray) else { return nil }
        var pixels = [UInt8](repeating: 0, count: width * height)
        let drawn: Bool = pixels.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(data: raw.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width, space: space,
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            // Flip so buffer row 0 is the top row, matching screen coordinates.
            context.translateBy(x: 0, y: CGFloat(height))
            context.scaleBy(x: 1, y: -1)
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        return Gray(pixels: pixels, width: width, height: height)
    }
}
