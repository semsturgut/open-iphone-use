import XCTest
@testable import OpenIPhoneUseCore

final class PointerCalibrationTests: XCTestCase {
    func testUncalibratedCalibrationReportsNothing() {
        let calibration = PointerCalibration()
        XCTAssertFalse(calibration.isCalibrated)
        XCTAssertNil(calibration.units(for: 0.5, axis: .x))
        XCTAssertNil(calibration.fraction(forUnits: 100, axis: .y))
        XCTAssertEqual(calibration.corrected(0.42, axis: .x), 0.42, "with no samples the target passes through")
    }

    func testSessionReuseRequiresValidatedMatchingScreen() {
        var calibration = PointerCalibration(span: .init(width: 800, height: 1700))
        calibration.setScreenAspectRatio(0.462)
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"), "a span alone is not held-out validation")
        calibration.setQuality(0.0033)
        calibration.setEvidence(.heldOut)
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"), "legacy calibrations need one device-bound measurement")
        calibration.bindDevice("phone-a")
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"), "changed pacing requires a new measurement")
        calibration.setMotionProfile(.turbo32)
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-b"))
        XCTAssertTrue(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"))
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 2.164, deviceIdentifier: "phone-a"), "rotation must trigger calibration")
        XCTAssertFalse(calibration.canReuse(forScreenAspect: nil, deviceIdentifier: "phone-a"))
        calibration.setQuality(0.026)
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"))
        calibration.setQuality(.nan)
        XCTAssertFalse(calibration.canReuse(forScreenAspect: 0.462, deviceIdentifier: "phone-a"))
    }

    func testMotionProfileAndDeviceBindingSurvivePersistence() throws {
        var model = PointerCalibration(span: .init(width: 700, height: 1400), quality: 0.01)
        model.setEvidence(.heldOut)
        model.bindDevice("phone-a")
        model.setScreenAspectRatio(0.5)
        model.setMotionProfile(.fast16)
        let restored = try JSONDecoder().decode(PointerCalibration.self, from: JSONEncoder().encode(model))
        XCTAssertEqual(restored.motionProfile, .fast16)
        XCTAssertTrue(restored.canReuse(forScreenAspect: 0.5, deviceIdentifier: "phone-a"))
    }

    func testNonlinearCurveDoesNotExtrapolateTheEarlyRampToTheBottom() throws {
        var model = PointerCalibration(span: .init(width: 220, height: 556))
        for point in [(64.0, 0.205), (224.0, 0.493), (367.0, 0.946)] {
            model.remember(.y, aimed: point.0 / 556, landed: point.1)
        }
        XCTAssertEqual(Double(try XCTUnwrap(model.units(for: 0.75, axis: .y))), 305, accuracy: 1)
        XCTAssertEqual(Double(try XCTUnwrap(model.units(for: 0.94, axis: .y))), 365, accuracy: 1)
        XCTAssertGreaterThan(try XCTUnwrap(model.units(for: 0.05, axis: .y)), 0)
    }

    func testTwoPointEstimateHasNoInventedValidationScore() throws {
        var model = try XCTUnwrap(PointerCalibration.twoPointEstimate(
            .init(unitsX: 128, unitsY: 256, x: 0.15, y: 0.14),
            .init(unitsX: 640, unitsY: 1152, x: 0.75, y: 0.70)))
        XCTAssertNil(model.quality)
        XCTAssertEqual(model.evidence, .twoPointEstimate)
        model.bindDevice("phone-a")
        model.setScreenAspectRatio(0.5)
        model.setMotionProfile(.burst127)
        XCTAssertTrue(model.canReuse(forScreenAspect: 0.5, deviceIdentifier: "phone-a"))
        XCTAssertEqual(Double(try XCTUnwrap(model.units(for: 0.94, axis: .y))), 1536, accuracy: 2)
        model.setQuality(0)
        XCTAssertFalse(model.canReuse(forScreenAspect: 0.5, deviceIdentifier: "phone-a"), "two-point RMS must never masquerade as validation")
    }

    func testTwoPointEstimateRejectsClampingSmallBaselineAndNonfiniteReadings() {
        let first = PointerCalibration.Observation(unitsX: 128, unitsY: 256, x: 0.15, y: 0.14)
        for second in [
            PointerCalibration.Observation(unitsX: 640, unitsY: 1152, x: 1, y: 0.70),
            .init(unitsX: 640, unitsY: 1152, x: 0.3, y: 0.70),
            .init(unitsX: 640, unitsY: 1152, x: 0.75, y: .nan),
            .init(unitsX: 64, unitsY: 1152, x: 0.75, y: 0.70)
        ] { XCTAssertNil(PointerCalibration.twoPointEstimate(first, second)) }
    }

    func testSpanFromTwoStopsMatchesTheMeasuredRatio() {
        // 200 units moved between the stops, landing 0.4 of the width apart.
        let span = PointerCalibration.span(first: (units: 150, x: 0.20, y: 0.15),
                                           second: (units: 350, x: 0.60, y: 0.55))
        XCTAssertEqual(span?.width, 500)
        XCTAssertEqual(span?.height, 500)
    }

    func testSpanFromTwoStopsRejectsUnusableGeometry() {
        XCTAssertNil(PointerCalibration.span(first: (units: 150, x: 0.5, y: 0.5),
                                             second: (units: 350, x: 0.5, y: 0.5)),
                     "no movement means no scale")
        XCTAssertNil(PointerCalibration.span(first: (units: 350, x: 0.6, y: 0.6),
                                             second: (units: 150, x: 0.2, y: 0.2)),
                     "the second stop must be further from home")
    }

    func testSpanFromOneLanding() {
        XCTAssertEqual(PointerCalibration.span(fromUnits: 200, landedAt: 0.5)?.width, 400)
        XCTAssertNil(PointerCalibration.span(fromUnits: 200, landedAt: 0.99),
                     "a landing at the edge is a clamped pointer, not a measurement")
        XCTAssertNil(PointerCalibration.span(fromUnits: 0, landedAt: 0.5))
    }

    func testUnitsAndFractionsRoundTrip() {
        var calibration = PointerCalibration()
        calibration.apply(span: .init(width: 500, height: 1000))
        XCTAssertEqual(calibration.units(for: 0.5, axis: .x), 250)
        XCTAssertEqual(calibration.units(for: 0.5, axis: .y), 500)
        XCTAssertEqual(calibration.fraction(forUnits: 250, axis: .x), 0.5)
        XCTAssertEqual(calibration.units(for: 1.2, axis: .x), 600,
                       "a target below the page viewport must extrapolate past the calibrated span")
        XCTAssertEqual(calibration.units(for: 2.0, axis: .x), 675, "but nonsense is still bounded")
        XCTAssertEqual(calibration.units(for: -1.0, axis: .y), -350,
                       "a target above the page viewport extrapolates the same way")
    }

    func testLearnedCorrectionCompensatesAConsistentOffset() {
        var calibration = PointerCalibration()
        calibration.apply(span: .init(width: 500, height: 500))
        // Aiming at f lands 0.05 lower every time: landed = aimed + 0.05.
        for aimed in [0.2, 0.4, 0.6, 0.8] {
            calibration.remember(.x, aimed: aimed, landed: aimed + 0.05)
        }
        let corrected = calibration.corrected(0.5, axis: .x)
        XCTAssertEqual(corrected, 0.45, accuracy: 0.01, "the offset must be subtracted before aiming")
        XCTAssertEqual(calibration.drift, 0.05, accuracy: 0.01)
    }

    func testCorrectionIgnoresNoiseAndExtremeGains() {
        var calibration = PointerCalibration()
        calibration.apply(span: .init(width: 500, height: 500))
        // A wild scatter must not produce a gain the model would trust.
        calibration.remember(.x, aimed: 0.2, landed: 0.9)
        calibration.remember(.x, aimed: 0.8, landed: 0.1)
        XCTAssertEqual(calibration.corrected(0.5, axis: .x), 0.5, "a negative gain is rejected")
        XCTAssertEqual(calibration.corrected(0.5, axis: .y), 0.5, "the other axis is untouched")
    }

    func testSamplesAreBoundedAndPerAxis() {
        var calibration = PointerCalibration()
        calibration.apply(span: .init(width: 500, height: 500))
        for index in 0..<40 {
            calibration.remember(.x, aimed: Double(index) / 100, landed: Double(index) / 100)
        }
        XCTAssertEqual(calibration.samples(for: .x).count, PointerCalibration.maximumSamples)
        XCTAssertTrue(calibration.samples(for: .y).isEmpty)
    }

    func testFitReportsResidual() {
        let exact = PointerCalibration.fit([.init(aimed: 0, landed: 0), .init(aimed: 1, landed: 1)])
        XCTAssertEqual(exact?.a ?? 0, 1, accuracy: 0.0001)
        XCTAssertEqual(exact?.rms ?? 1, 0, accuracy: 0.0001)
        XCTAssertNil(PointerCalibration.fit([.init(aimed: 0.5, landed: 0.5)]), "one point cannot fit a line")
    }
}

final class PointerLocatorTests: XCTestCase {
    /// Draws a frame with a pointer blob at the given fraction, and returns JPEG data.
    private func frame(width: Int = 200, height: Int = 400, pointer: (x: Double, y: Double)?,
                       background: UInt8 = 60) throws -> Data {
        let bytesPerRow = width
        var pixels = [UInt8](repeating: background, count: width * height)
        if let pointer {
            let cx = Int(pointer.x * Double(width)), cy = Int(pointer.y * Double(height))
            for dy in -6...6 {
                for dx in -6...6 where (dx * dx) + (dy * dy) <= 36 {
                    let x = cx + dx, y = cy + dy
                    guard x >= 0, x < width, y >= 0, y < height else { continue }
                    pixels[(y * width) + x] = 220
                }
            }
        }
        return try jpeg(pixels: pixels, width: width, height: height, bytesPerRow: bytesPerRow)
    }

    private func jpeg(pixels: [UInt8], width: Int, height: Int, bytesPerRow: Int) throws -> Data {
        var mutable = pixels
        let data = mutable.withUnsafeMutableBytes { raw -> Data? in
            guard let space = CGColorSpace(name: CGColorSpace.genericGrayGamma2_2),
                  let context = CGContext(data: raw.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: bytesPerRow, space: space,
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue),
                  let image = context.makeImage() else { return nil }
            let output = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(output, "public.jpeg" as CFString, 1, nil) else { return nil }
            CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 1.0] as CFDictionary)
            guard CGImageDestinationFinalize(destination) else { return nil }
            return output as Data
        }
        return try XCTUnwrap(data)
    }

    func testFindsThePointerWhereOnlyTheBlobMoved() throws {
        let before = try frame(pointer: (x: 0.50, y: 0.50))
        let after = try frame(pointer: (x: 0.56, y: 0.50))
        let found = PointerLocator.locate(before: before, after: after,
                                          search: .init(near: .init(x: 0.53, y: 0.50), reach: .init(x: 0.15, y: 0.10)),
                                          shift: 0.06)
        let point = try XCTUnwrap(found)
        // The blob midpoint is 0.53, so the pointer's start is half a shift to the left.
        XCTAssertEqual(point.x, 0.50, accuracy: 0.02)
        XCTAssertEqual(point.y, 0.50, accuracy: 0.02)
        XCTAssertTrue(PointerLocator.usable(point))
    }

    func testRejectsAWholeScreenChange() throws {
        let before = try frame(pointer: nil, background: 40)
        let after = try frame(pointer: nil, background: 200)
        XCTAssertNil(PointerLocator.locate(before: before, after: after,
                                           search: .init(near: .init(x: 0.5, y: 0.5), reach: .init(x: 0.5, y: 0.5))),
                     "a global change is not a pointer")
    }

    func testRejectsAnUnchangedFramePair() throws {
        let same = try frame(pointer: (x: 0.4, y: 0.4))
        XCTAssertNil(PointerLocator.locate(before: same, after: same),
                     "nothing moved, so nothing is found")
    }

    func testIgnoresMovementOutsideTheSearchWindow() throws {
        let before = try frame(pointer: (x: 0.20, y: 0.50))
        let after = try frame(pointer: (x: 0.26, y: 0.50))
        XCTAssertNil(PointerLocator.locate(before: before, after: after,
                                           search: .init(near: .init(x: 0.8, y: 0.8), reach: .init(x: 0.1, y: 0.1))),
                     "the search window must actually restrict the search")
    }

    func testIgnoresTheStatusBar() throws {
        let before = try frame(pointer: (x: 0.50, y: 0.02))
        let after = try frame(pointer: (x: 0.56, y: 0.02))
        XCTAssertNil(PointerLocator.locate(before: before, after: after,
                                           search: .init(near: .init(x: 0.53, y: 0.02), reach: .init(x: 0.2, y: 0.10))),
                     "the clock lives there and changes on its own")
    }

    func testUsableRejectsEdgeReadings() {
        XCTAssertFalse(PointerLocator.usable(.init(x: 0.01, y: 0.5)))
        XCTAssertFalse(PointerLocator.usable(.init(x: 0.5, y: 0.99)))
        XCTAssertTrue(PointerLocator.usable(.init(x: 0.5, y: 0.5)))
    }
}

final class ViewportMarkersTests: XCTestCase {
    /// Draws a frame with a red border rectangle, like the calibration board.
    private func frame(width: Int = 200, height: Int = 400, top: Double, bottom: Double, recordingBadge: Bool = false) throws -> Data {
        var pixels = [UInt8](repeating: 255, count: width * height * 4)
        func mark(_ x: Int, _ y: Int) {
            guard x >= 0, x < width, y >= 0, y < height else { return }
            let i = ((y * width) + x) * 4
            // #00e07a: the board's marks are green so they cannot be confused with the red
            // recording clock iOS draws while a broadcast runs.
            pixels[i] = 0; pixels[i + 1] = 224; pixels[i + 2] = 122; pixels[i + 3] = 255
        }
        if recordingBadge {
            for y in 6...18 { for x in 20...65 { mark(x, y) } }
        }
        let y0 = Int(top * Double(height)), y1 = Int(bottom * Double(height))
        for x in 0..<width { for t in 0..<3 { mark(x, y0 + t); mark(x, y1 - t) } }
        for y in y0...y1 { for t in 0..<3 { mark(t, y); mark(width - 1 - t, y) } }
        return try jpeg(pixels: pixels, width: width, height: height)
    }

    private func jpeg(pixels: [UInt8], width: Int, height: Int) throws -> Data {
        var mutable = pixels
        let data = mutable.withUnsafeMutableBytes { raw -> Data? in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: raw.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
                  let image = context.makeImage() else { return nil }
            let output = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(output, "public.jpeg" as CFString, 1, nil) else { return nil }
            CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 1.0] as CFDictionary)
            guard CGImageDestinationFinalize(destination) else { return nil }
            return output as Data
        }
        return try XCTUnwrap(data)
    }

    func testMeasuresTheBorderRectangle() throws {
        let data = try frame(top: 0.10, bottom: 0.90)
        let rect = try XCTUnwrap(ViewportMarkers.locate(jpeg: data))
        XCTAssertEqual(rect.top, 0.10, accuracy: 0.02)
        XCTAssertEqual(rect.top + rect.height, 0.90, accuracy: 0.02)
        XCTAssertEqual(rect.left, 0.0, accuracy: 0.02)
        XCTAssertEqual(rect.left + rect.width, 1.0, accuracy: 0.02)
        XCTAssertTrue(rect.isValid)
    }

    func testRecordingBadgeDoesNotExtendTheBoard() throws {
        let rect = try XCTUnwrap(ViewportMarkers.locate(jpeg: frame(top: 0.10, bottom: 0.85, recordingBadge: true)))
        XCTAssertEqual(rect.top, 0.10, accuracy: 0.015)
        XCTAssertEqual(rect.height, 0.75, accuracy: 0.02)
    }

    func testCalibrationIsSavedInScreenCoordinatesBeyondSafariBottom() throws {
        let board = ViewportMarkers.Rect(top: 0.056, height: 0.828, left: 0, width: 1)
        let firstY = board.screenFraction(forViewport: 0.20, axis: .y)
        let secondY = board.screenFraction(forViewport: 0.60, axis: .y)
        let span = try XCTUnwrap(PointerCalibration.span(first: (units: 150, x: 0.25, y: firstY),
                                                       second: (units: 350, x: 0.65, y: secondY)))
        var calibration = PointerCalibration()
        calibration.apply(span: span)
        calibration.remember(.y, aimed: 150 / Double(span.height), landed: firstY)
        calibration.remember(.y, aimed: 350 / Double(span.height), landed: secondY)
        let saved = try JSONDecoder().decode(PointerCalibration.self, from: JSONEncoder().encode(calibration))
        // The physical y=0.94 is below the board, but must still produce the extrapolated unit offset.
        let expected = 150 + (0.94 - firstY) / (secondY - firstY) * 200
        XCTAssertEqual(Double(try XCTUnwrap(saved.units(for: 0.94, axis: .y))), expected, accuracy: 1)
        XCTAssertEqual(saved.coordinateSpaceVersion, 2)
    }

    func testOldViewportCalibrationCannotBeLoadedAsScreenCalibration() {
        let old = Data(#"{"span":{"width":632,"height":1134},"samples":{}}"#.utf8)
        XCTAssertThrowsError(try JSONDecoder().decode(PointerCalibration.self, from: old))
    }

    func testThinBorderBetweenStrideRowsIsStillMeasured() throws {
        let rect = try XCTUnwrap(ViewportMarkers.locate(jpeg: frame(top: 0.103, bottom: 0.853)))
        XCTAssertEqual(rect.top, 0.103, accuracy: 0.015)
        XCTAssertEqual(rect.top + rect.height, 0.853, accuracy: 0.015)
    }

    func testConvertsBetweenScreenAndViewport() {
        let rect = ViewportMarkers.Rect(top: 0.10, height: 0.80, left: 0, width: 1)
        XCTAssertEqual(rect.viewportFraction(forScreen: 0.50, axis: .y), 0.5, accuracy: 0.001)
        XCTAssertEqual(rect.viewportFraction(forScreen: 0.90, axis: .y), 1.0, accuracy: 0.001)
        XCTAssertEqual(rect.viewportFraction(forScreen: 0.94, axis: .y), 1.05, accuracy: 0.001,
                       "a target below the page must extrapolate past the calibrated span")
        XCTAssertEqual(rect.screenFraction(forViewport: 0.5, axis: .y), 0.5, accuracy: 0.001)
        XCTAssertEqual(rect.viewportFraction(forScreen: 0.42, axis: .x), 0.42, accuracy: 0.001)
    }

    func testRejectsAFrameWithoutABorder() throws {
        let plain = try jpeg(pixels: [UInt8](repeating: 250, count: 200 * 400 * 4), width: 200, height: 400)
        XCTAssertNil(ViewportMarkers.locate(jpeg: plain), "a blank frame has no board on it")
    }
}
