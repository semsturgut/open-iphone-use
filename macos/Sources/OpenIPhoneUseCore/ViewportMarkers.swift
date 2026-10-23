import CoreGraphics
import Foundation
import ImageIO

/// Finds the calibration board's red border inside a captured frame.
///
/// The board only covers the page viewport, which is smaller than the screen, and the page's
/// own `visualViewport` numbers do not match what is actually rendered (measured: the board
/// spans 0.013...0.883 of the frame while the page reported 0.000...0.828). The border drawn
/// on the board is therefore the only trustworthy source for where the page really is, and it
/// is what lets a screen fraction be converted into the viewport fraction the board measures.
public enum ViewportMarkers {
    /// The page viewport as fractions of the whole screen.
    public struct Rect: Sendable, Equatable {
        public let top: Double
        public let height: Double
        public let left: Double
        public let width: Double

        public init(top: Double, height: Double, left: Double, width: Double) {
            self.top = top
            self.height = height
            self.left = left
            self.width = width
        }

        public var isValid: Bool {
            height > 0.2 && height <= 1.01 && width > 0.2 && width <= 1.01 &&
            top >= -0.01 && top + height <= 1.02 && left >= -0.01 && left + width <= 1.02
        }

        /// Screen fraction -> viewport fraction.
        public func viewportFraction(forScreen fraction: Double, axis: Axis) -> Double {
            guard isValid else { return fraction }
            return axis == .x ? (fraction - left) / width : (fraction - top) / height
        }

        /// Viewport fraction -> screen fraction.
        public func screenFraction(forViewport fraction: Double, axis: Axis) -> Double {
            guard isValid else { return fraction }
            return axis == .x ? left + (fraction * width) : top + (fraction * height)
        }
    }

    public enum Axis: Sendable { case x, y }

    /// Scans a JPEG for the board's red border and returns the rectangle it encloses.
    ///
    /// Sampling is coarse on purpose: the border is a solid line several pixels wide, so a
    /// stride of four finds it while keeping the scan cheap enough to run per frame.
    public static func locate(jpeg: Data, stride: Int = 4) -> Rect? {
        guard let pixels = rgb(jpeg: jpeg) else { return nil }
        let width = pixels.width, height = pixels.height
        // Find long horizontal border lines, not the bounding box of every red pixel.
        // The board's marks are #00e07a. Green rather than red because iOS draws a red recording
        // clock at the top of the screen while a broadcast runs, which a red border has to be
        // told apart from; nothing else on the screen is this green.
        func marked(_ x: Int, _ y: Int) -> Bool {
            let i = ((y * width) + x) * 4
            let r = Int(pixels.bytes[i]), g = Int(pixels.bytes[i + 1]), b = Int(pixels.bytes[i + 2])
            return g > 180 && r < 120 && b < 160 && g - max(r, b) > 70
        }
        let step = max(1, stride)
        var rows: [(y: Int, left: Int, right: Int)] = []
        for y in 0..<height {
            var count = 0, left = width, right = 0
            for x in Swift.stride(from: 0, to: width, by: step) where marked(x, y) {
                count += 1; left = min(left, x); right = max(right, x)
            }
            if Double(count * step) >= Double(width) * 0.85 {
                rows.append((y, left, right))
            }
        }
        guard let first = rows.first, let last = rows.last,
              last.y - first.y > height / 3 else { return nil }
        let left = min(first.left, last.left), right = max(first.right, last.right)
        // Both side edges must also be present. A red photograph is not the board.
        var sideHits = 0, samples = 0
        for y in Swift.stride(from: first.y, through: last.y, by: step) {
            samples += 1
            let hasLeft = (max(0, left - step)...min(width - 1, left + step)).contains { marked($0, y) }
            let hasRight = (max(0, right - step)...min(width - 1, right + step)).contains { marked($0, y) }
            if hasLeft && hasRight { sideHits += 1 }
        }
        guard Double(sideHits) >= Double(samples) * 0.75 else { return nil }
        let slack = 0.5
        let x0 = max(0, Double(left) - slack), x1 = min(Double(width), Double(right) + slack)
        let y0 = max(0, Double(first.y) - slack), y1 = min(Double(height), Double(last.y) + slack)
        let rect = Rect(top: y0 / Double(height), height: (y1 - y0) / Double(height),
                        left: x0 / Double(width), width: (x1 - x0) / Double(width))
        return rect.isValid ? rect : nil
    }

    struct Pixels {
        let bytes: [UInt8]
        let width: Int
        let height: Int
    }

    /// Decodes a JPEG into tightly packed RGBA bytes.
    static func rgb(jpeg: Data) -> Pixels? {
        guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        let width = image.width, height = image.height
        guard width > 0, height > 0 else { return nil }
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drawn: Bool = bytes.withUnsafeMutableBytes { raw in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: raw.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return drawn ? Pixels(bytes: bytes, width: width, height: height) : nil
    }
}
