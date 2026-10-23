import CoreGraphics
import ImageIO
import XCTest
@testable import OpenIPhoneUseCore

final class BoardPointerLocatorTests: XCTestCase {
    private let board = ViewportMarkers.Rect(top: 0.1, height: 0.75, left: 0, width: 1)
    private func jpeg(disks: [(Int, Int)], ring: Bool = false, crossingGrid: Bool = false) throws -> Data {
        let w = 200, h = 400
        var pixels = [UInt8](repeating: 255, count: w * h * 4)
        for (cx, cy) in disks { for y in max(0,cy-6)...min(h-1,cy+6) { for x in max(0,cx-6)...min(w-1,cx+6) {
            let d = (x-cx)*(x-cx)+(y-cy)*(y-cy)
            guard d <= 25, !ring || d >= 18 else { continue }
            let i = (y*w+x)*4
            pixels[i] = 145; pixels[i+1] = 145; pixels[i+2] = 145
        } } }
        if crossingGrid {
            for y in 40..<340 { for x in 39...41 {
                let i = (y*w+x)*4; pixels[i] = 155; pixels[i+1] = 155; pixels[i+2] = 155
            } }
            for y in 119...121 { for x in 0..<w {
                let i = (y*w+x)*4; pixels[i] = 155; pixels[i+1] = 155; pixels[i+2] = 155
            } }
        }
        // Dark button / grey grid: neither may be mistaken for a cursor.
        for y in 45..<65 { for x in 160..<190 {
            let i = (y*w+x)*4; pixels[i] = 15; pixels[i+1] = 15; pixels[i+2] = 15
        } }
        for y in 40..<340 { let i = (y*w+100)*4; pixels[i] = 215; pixels[i+1] = 215; pixels[i+2] = 215 }
        return try pixels.withUnsafeMutableBytes { raw in
            let context = try XCTUnwrap(CGContext(data: raw.baseAddress, width: w, height: h,
                bitsPerComponent: 8, bytesPerRow: w*4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
            let output = NSMutableData()
            let destination = try XCTUnwrap(CGImageDestinationCreateWithData(output, "public.jpeg" as CFString, 1, nil))
            CGImageDestinationAddImage(destination, try XCTUnwrap(context.makeImage()),
                                      [kCGImageDestinationLossyCompressionQuality: 1.0] as CFDictionary)
            XCTAssertTrue(CGImageDestinationFinalize(destination))
            return output as Data
        }
    }
    func testFindsACompactPointerOnlyInsideTheWhiteBoard() throws {
        let found = try XCTUnwrap(BoardPointerLocator.locate(jpeg: jpeg(disks: [(40,120)]), board: board))
        XCTAssertEqual(found.x, 0.2, accuracy: 0.01)
        XCTAssertEqual(found.y, 0.3, accuracy: 0.01)
    }
    func testFindsPointerTouchingNeutralGridLines() throws {
        let point = try XCTUnwrap(BoardPointerLocator.locate(jpeg: jpeg(disks: [(40,120)], crossingGrid: true), board: board))
        XCTAssertEqual(point.x, 0.2, accuracy: 0.015)
        XCTAssertEqual(point.y, 0.3, accuracy: 0.015)
    }
    func testRejectsStatusBarPointerAmbiguityAndMissingPointer() throws {
        XCTAssertNil(BoardPointerLocator.locate(jpeg: try jpeg(disks: [(40,10)]), board: board))
        XCTAssertNil(BoardPointerLocator.locate(jpeg: try jpeg(disks: []), board: board))
        XCTAssertNil(BoardPointerLocator.locate(jpeg: try jpeg(disks: [(40,120),(140,220)]), board: board))
        XCTAssertNil(BoardPointerLocator.locate(jpeg: try jpeg(disks: [(40,120)], ring: true), board: board))
    }
}
