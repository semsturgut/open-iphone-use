import AppKit
import XCTest
@testable import OpenIPhoneUseCore

final class ScreenshotDimensionsTests: XCTestCase {
    func testFrameReportsOriginalJPEGDimensions() throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 9, pixelsHigh: 19,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let jpeg = try XCTUnwrap(bitmap.representation(using: .jpeg, properties: [:]))
        let frame = ScreenshotFrameWire(frameID: "frame", capturedAt: Date(), ageMilliseconds: 0,
                                        captureActive: true, mimeType: "image/jpeg", jpeg: jpeg)
        XCTAssertEqual(frame.width, 9)
        XCTAssertEqual(frame.height, 19)
    }
}
