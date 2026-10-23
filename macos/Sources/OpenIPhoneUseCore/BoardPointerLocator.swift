import Foundation

/// Only for our white calibration board, never for arbitrary app/video screens.
/// A compact neutral filled disk must be visible before a calibration click is allowed.
public enum BoardPointerLocator {
    public static func locate(jpeg: Data, board: ViewportMarkers.Rect) -> PointerLocator.Point? {
        guard board.isValid, let image = ViewportMarkers.rgb(jpeg: jpeg) else { return nil }
        let w = image.width, h = image.height
        let x0 = max(1, Int((board.left + 0.01) * Double(w)))
        let x1 = min(w - 1, Int((board.left + board.width - 0.01) * Double(w)))
        let y0 = max(1, Int((board.top + 0.01) * Double(h)))
        let y1 = min(h - 1, Int((board.top + board.height - 0.01) * Double(h)))
        guard x1 > x0, y1 > y0 else { return nil }
        var mask = [UInt8](repeating: 0, count: w * h)
        for y in y0..<y1 { for x in x0..<x1 {
            let i = (y * w + x) * 4
            let r = Int(image.bytes[i]), g = Int(image.bytes[i + 1]), b = Int(image.bytes[i + 2])
            if (80...190).contains(r), abs(r - g) < 10, abs(r - b) < 10 { mask[y * w + x] = 1 }
        } }
        // Thin neutral grid lines can join the pointer into a huge component.
        // Erode them away before measuring compact disk cores; do not broaden color matching.
        let original = mask, radius = max(2, w / 250)
        for y in y0..<y1 { for x in x0..<x1 where original[y * w + x] == 1 {
            var solid = x >= radius && y >= radius && x + radius < w && y + radius < h
            if solid {
                outer: for oy in -radius...radius { for ox in -radius...radius {
                    if original[(y + oy) * w + x + ox] == 0 { solid = false; break outer }
                } }
            }
            if !solid { mask[y * w + x] = 0 }
        } }
        var candidates: [PointerLocator.Point] = []
        for y in y0..<y1 { for x in x0..<x1 where mask[y * w + x] == 1 {
            var queue = [y * w + x], cursor = 0
            mask[queue[0]] = 0
            var minX = x, maxX = x, minY = y, maxY = y, sumX = 0, sumY = 0
            while cursor < queue.count {
                let i = queue[cursor]; cursor += 1
                let px = i % w, py = i / w
                minX = min(minX, px); maxX = max(maxX, px)
                minY = min(minY, py); maxY = max(maxY, py)
                sumX += px; sumY += py
                for next in [i - 1, i + 1, i - w, i + w] where next >= 0 && next < mask.count && mask[next] == 1 {
                    mask[next] = 0; queue.append(next)
                }
            }
            let cw = maxX - minX + 1, ch = maxY - minY + 1
            let aspect = Double(cw) / Double(ch), fill = Double(queue.count) / Double(cw * ch)
            guard cw >= max(5, w / 120), ch >= max(5, w / 120),
                  cw <= w / 12, ch <= w / 12,
                  (0.8...1.25).contains(aspect), (0.66...0.90).contains(fill) else { continue }
            candidates.append(.init(x: Double(sumX) / Double(queue.count * w),
                                    y: Double(sumY) / Double(queue.count * h)))
        } }
        return candidates.count == 1 ? candidates[0] : nil
    }
}
