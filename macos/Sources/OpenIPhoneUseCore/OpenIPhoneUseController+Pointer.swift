import Foundation
import ImageIO

/// Closed-loop pointer control on top of the relative BLE HID mouse.
///
/// A relative pointer has no position, so the app homes it into the top-left corner and
/// counts mouse units from there, cancelling drift by re-homing every few moves. The units
/// needed to cross the screen are measured, not assumed, because iOS applies its own
/// pointer acceleration. Every landing the calibration board catches refines the mapping,
/// and the pointer can be found on screen by wiggling it and diffing two frames.
extension OpenIPhoneUseController {
    // MARK: - calibration persistence

    func loadPointerCalibration() {
        guard let data = UserDefaults.standard.data(forKey: Self.pointerCalibrationKey),
              let saved = try? JSONDecoder().decode(PointerCalibration.self, from: data), saved.isCalibrated else { return }
        pointerCalibration = saved
        pointerMotionProfile = saved.motionProfile ?? .turbo32
        refreshPointerNote()
    }

    func savePointerCalibration() {
        guard let data = try? JSONEncoder().encode(pointerCalibration) else { return }
        UserDefaults.standard.set(data, forKey: Self.pointerCalibrationKey)
        refreshPointerNote()
    }

    private func refreshPointerNote() {
        guard let span = pointerCalibration.span else {
            pointerNote = "Pointer not calibrated."
            return
        }
        let quality = pointerCalibration.quality.map { String(format: " validation miss %.2f%%", $0 * 100) } ?? ""
        pointerNote = "Pointer span \(span.width)×\(span.height) units\(quality); motion \(pointerMotionProfile.rawValue)."
    }

    // MARK: - motion

    /// Cancels drift by slamming the pointer into the corner, which iOS clamps.
    public func pointerHome() async throws {
        try await hid.home()
        try await Task.sleep(for: .milliseconds(60))
        pointerUnits = (0, 0)
    }

    /// The calibration and production paths use identical fixed-size, paced axis reports.
    func sendPointerDelta(dx: Int, dy: Int, steps: Int? = nil) async throws {
        try await hid.moveCalibrated(dx: dx, dy: dy, profile: pointerMotionProfile)
    }

    /// Absolute moves start from home so edge clamping and raw diagnostic moves cannot
    /// corrupt the tracked origin. Relative corrections may opt out explicitly.
    func movePointerToUnits(x: Int, y: Int, rehome: Bool = true) async throws {
        if pointerUnits == nil || rehome {
            try await pointerHome()
        }
        let current = pointerUnits ?? (0, 0)
        let dx = x - current.x, dy = y - current.y
        pointerUnits = nil // unknown until the move succeeds
        try await sendPointerDelta(dx: dx, dy: dy)
        pointerUnits = (x, y)
    }

    // MARK: - frames

    /// Waits until the capture stops producing new frames, so a wiggle diff is not polluted
    /// by unrelated screen changes.
    func settlePointerFrames(quietMilliseconds: Int = 200, leastMilliseconds: Int = 150,
                             mostMilliseconds: Int = 1_500) async {
        let start = Date()
        var lastID = await frames.latestFrame()?.frameID
        var lastChange = Date()
        while Date().timeIntervalSince(start) * 1_000 < Double(mostMilliseconds) {
            try? await Task.sleep(for: .milliseconds(60))
            let current = await frames.latestFrame()?.frameID
            if current != lastID {
                lastID = current
                lastChange = Date()
            }
            let quietFor = Date().timeIntervalSince(lastChange) * 1_000
            let elapsed = Date().timeIntervalSince(start) * 1_000
            if elapsed >= Double(leastMilliseconds), quietFor >= Double(quietMilliseconds) { return }
        }
    }

    func waitForFreshFrame(since frameID: String, timeoutMilliseconds: Int = 1_200) async -> ScreenshotFrame? {
        let deadline = Date().addingTimeInterval(Double(timeoutMilliseconds) / 1_000)
        while Date() < deadline {
            if let latest = await frames.latestFrame(), latest.frameID != frameID { return latest }
            try? await Task.sleep(for: .milliseconds(50))
        }
        return nil
    }

    // MARK: - locating

    /// Finds the pointer by nudging it a known distance and diffing the frames around it.
    ///
    /// Returns the position as a screen fraction, or nil when the pointer could not be
    /// isolated. The nudge is undone before returning, so the tracked position stays valid.
    public func locatePointer(near: PointerLocator.Point? = nil,
                              reach: PointerLocator.Point? = nil) async throws -> PointerLocator.Point? {
        guard isConnected else { throw BLEHIDError.notConnected }
        await settlePointerFrames()
        guard let before = await frames.latestFrame() else { return nil }

        let wiggle = Self.pointerWiggleUnits
        let shift = pointerCalibration.fraction(forUnits: wiggle, axis: .x) ?? 0
        let search = PointerLocator.Search(near: near ?? .init(x: 0.5, y: 0.5),
                                           reach: reach ?? .init(x: 0.5, y: 0.5))
        try await hid.moveSmooth(dx: wiggle, dy: 0, steps: 1)
        var answer: PointerLocator.Point?
        do {
            let deadline = Date().addingTimeInterval(3)
            var lastID = before.frameID
            var previous: PointerLocator.Point?
            var stableSince: Date?
            while Date() < deadline {
                try Task.checkCancellation()
                if let frame = await frames.latestFrame(), frame.frameID != lastID,
                   await frames.ageMilliseconds(of: frame) < 1_000 {
                    lastID = frame.frameID
                    if let point = PointerLocator.locate(before: before.jpeg, after: frame.jpeg, search: search, shift: shift) {
                        if let previous, hypot(previous.x-point.x, previous.y-point.y) <= 0.003 {
                            if let stableSince, frame.capturedAt.timeIntervalSince(stableSince) >= 0.12 { answer = point; break }
                        } else { stableSince = frame.capturedAt }
                        previous = point
                    } else { previous = nil; stableSince = nil }
                }
                try await Task.sleep(for: .milliseconds(40))
            }
        } catch {
            try? await hid.moveSmooth(dx: -wiggle, dy: 0, steps: 1)
            throw error
        }
        try await hid.moveSmooth(dx: -wiggle, dy: 0, steps: 1)
        return answer
    }

    /// Finds the pointer near where it is expected, widening the search if that fails.
    func locatePointer(expectedX: Double, expectedY: Double) async throws -> PointerLocator.Point? {
        let near = PointerLocator.Point(x: expectedX, y: expectedY)
        if let found = try await locatePointer(near: near, reach: .init(x: 0.18, y: 0.12)),
           PointerLocator.usable(found) {
            return found
        }
        return nil
    }

    // MARK: - calibration

    /// Automatic calibration: home, move a known distance, find the pointer, fit the span.
    @discardableResult
    public func calibratePointer() async throws -> PointerCalibration.Span {
        guard isConnected else { throw BLEHIDError.notConnected }
        guard await frames.isCaptureActive() else { throw ControlFrameError.captureInactive }

        try await pointerHome()
        var found: [(units: Int, point: PointerLocator.Point)] = []
        var previous = 0
        for stop in Self.pointerCalibrationStops {
            let step = stop - previous
            try await sendPointerDelta(dx: step, dy: step)
            pointerUnits = (stop, stop)
            previous = stop
            if let point = try await locatePointer(near: .init(x: 0.3, y: 0.3), reach: .init(x: 0.35, y: 0.35)),
               PointerLocator.usable(point) {
                found.append((stop, point))
            }
        }
        guard found.count >= 2, let span = PointerCalibration.span(first: (units: found[0].units, x: found[0].point.x, y: found[0].point.y),
                                                                   second: (units: found[1].units, x: found[1].point.x, y: found[1].point.y)) else {
            throw PointerControlError.notFound
        }
        pointerCalibration.apply(span: span)
        for sample in found {
            let aimedX = Double(sample.units) / Double(span.width)
            let aimedY = Double(sample.units) / Double(span.height)
            pointerCalibration.remember(.x, aimed: aimedX, landed: sample.point.x)
            pointerCalibration.remember(.y, aimed: aimedY, landed: sample.point.y)
        }
        savePointerCalibration()
        pointerUnits = nil
        return span
    }

    /// Two measured clicks, with no profile search or extra hover tour.
    /// The visible board gates each press; the result is an estimate, not held-out proof.
    @discardableResult
    public func quickProbePointer(profile: PointerMotion.Profile = .fast16) async throws -> PointerCalibration.Span {
        guard isConnected else { throw BLEHIDError.notConnected }
        _ = try await calibrationViewport()
        pointerCalibration = PointerCalibration()
        calibrationDiagnostics = []
        pointerMotionProfile = profile
        var succeeded = false
        defer { if !succeeded { pointerCalibration = PointerCalibration(); savePointerCalibration() } }
        let stops: [(Int, Int)]
        switch profile {
        case .turbo32, .burst127: stops = [(32, 64), (128, 256)]
        case .fast16: stops = [(64, 128), (192, 384)]
        case .balanced8: stops = [(128, 256), (384, 768)]
        case .precise4: stops = [(256, 512), (640, 1280)]
        }
        var observations: [PointerCalibration.Observation] = []
        for (ux, uy) in stops {
            try Task.checkCancellation()
            try await movePointerToUnits(x: ux, y: uy)
            // Require the same settled, board-contained pointer as other calibration presses.
            _ = try await observeBoardPointer()
            let sent = Date()
            try await hid.click(button: "left")
            guard let tap = await waitForTap(since: sent) else { throw PointerControlError.noTaps }
            let rect = try await calibrationViewport()
            let x = rect.screenFraction(forViewport: tap.x, axis: .x)
            let y = rect.screenFraction(forViewport: tap.y, axis: .y)
            observations.append(.init(unitsX: ux, unitsY: uy, x: x, y: y))
            calibrationDiagnostics.append(String(format: "%@ click %d,%d -> %.4f,%.4f", profile.rawValue, ux, uy, x, y))
        }
        guard var model = PointerCalibration.twoPointEstimate(observations[0], observations[1]),
              let span = model.span else { throw PointerControlError.inaccurateCalibration }
        model.setMotionProfile(profile)
        model.bindDevice(centralIdentifier)
        if let frame = await frames.latestFrame(), let aspect = frameAspect(frame) {
            model.setScreenAspectRatio(aspect)
        }
        pointerCalibration = model
        savePointerCalibration()
        succeeded = true
        pointerUnits = nil
        return span
    }

    public func probePointer(stops: [Int]? = nil) async throws -> PointerCalibration.Span {
        guard isConnected else { throw BLEHIDError.notConnected }
        guard await frames.isCaptureActive() else { throw ControlFrameError.captureInactive }

        let viewport = try await calibrationViewport()
        // Discard old viewport samples even when a fresh probe fails partway through.
        pointerCalibration = PointerCalibration()
        var calibrationSucceeded = false
        defer {
            if !calibrationSucceeded { pointerCalibration = PointerCalibration(); savePointerCalibration() }
        }
        let span: PointerCalibration.Span
        if stops == nil && pointerMotionProfile != .precise4 {
            span = try await quickProbePointer(profile: pointerMotionProfile)
        } else {
        var samples: [(units: Int, x: Double, y: Double)] = []
        let roughStops = stops ?? pointerMotionProfile.probeStops

        // A rough span from two diagonal probes, so later targets can be aimed sensibly.
        for stop in roughStops {
            try await pointerHome()
            try await movePointerToUnits(x: stop, y: stop, rehome: false)
            try Task.checkCancellation()
            try await requirePointerOnBoard()
            let sent = Date()
            try await hid.click(button: "left")
            guard let tap = await waitForTap(since: sent) else { throw PointerControlError.noTaps }
            samples.append((stop, viewport.screenFraction(forViewport: tap.x, axis: .x),
                                   viewport.screenFraction(forViewport: tap.y, axis: .y)))
        }

        guard samples.count >= 2 else { throw PointerControlError.noTaps }
        guard let measuredSpan = PointerCalibration.span(first: (units: samples[0].units, x: samples[0].x, y: samples[0].y),
                                                 second: (units: samples[1].units, x: samples[1].x, y: samples[1].y)) else {
            throw PointerControlError.notFound
        }
        span = measuredSpan
        pointerCalibration.apply(span: span)
        for sample in samples {
            pointerCalibration.remember(.x, aimed: Double(sample.units) / Double(span.width), landed: sample.x)
            pointerCalibration.remember(.y, aimed: Double(sample.units) / Double(span.height), landed: sample.y)
        }

        }

        // Fit against physical screen landings, using the units actually sent, not the
        // requested target (which may already include an earlier learned correction).
        for target in PointerCalibrationProbe.targets {
            guard let unitsX = pointerCalibration.units(for: target.x, axis: .x),
                  let unitsY = pointerCalibration.units(for: target.y, axis: .y) else { continue }
            try await pointerHome()
            try await movePointerToUnits(x: unitsX, y: unitsY, rehome: false)
            try Task.checkCancellation()
            try await requirePointerOnBoard()
            let sent = Date()
            try await hid.click(button: "left")
            guard let tap = await waitForTap(since: sent) else { throw PointerControlError.noTaps }
            pointerCalibration.remember(.x, aimed: Double(unitsX) / Double(span.width),
                                        landed: viewport.screenFraction(forViewport: tap.x, axis: .x))
            pointerCalibration.remember(.y, aimed: Double(unitsY) / Double(span.height),
                                        landed: viewport.screenFraction(forViewport: tap.y, axis: .y))
        }

        if let fitX = PointerCalibration.fit(pointerCalibration.samples(for: .x)),
           let fitY = PointerCalibration.fit(pointerCalibration.samples(for: .y)) {
            pointerCalibration.setQuality(max(fitX.rms, fitY.rms))
        }
        var worstValidationMiss = 0.0
        for target in [(0.40, 0.15), (0.60, 0.82)] {
            guard let unitsX = pointerCalibration.units(for: target.0, axis: .x),
                  let unitsY = pointerCalibration.units(for: target.1, axis: .y) else { continue }
            try await pointerHome()
            try await movePointerToUnits(x: unitsX, y: unitsY, rehome: false)
            try Task.checkCancellation()
            try await requirePointerOnBoard()
            let sent = Date()
            try await hid.click(button: "left")
            guard let tap = await waitForTap(since: sent) else { throw PointerControlError.noTaps }
            let landedX = viewport.screenFraction(forViewport: tap.x, axis: .x)
            let landedY = viewport.screenFraction(forViewport: tap.y, axis: .y)
            worstValidationMiss = max(worstValidationMiss, abs(landedX - target.0), abs(landedY - target.1))
        }
        pointerCalibration.setQuality(worstValidationMiss)
        pointerCalibration.setEvidence(.heldOut)
        guard worstValidationMiss <= 0.025 else { throw PointerControlError.inaccurateCalibration }
        if let frame = await frames.latestFrame(), let aspect = frameAspect(frame) {
            pointerCalibration.setScreenAspectRatio(aspect)
        }
        pointerCalibration.bindDevice(centralIdentifier)
        pointerCalibration.setMotionProfile(pointerMotionProfile)
        savePointerCalibration()
        calibrationSucceeded = true
        pointerUnits = nil
        return span
    }

    /// Observation, not an arbitrary long glide delay: never press on a status edge or
    /// notification sheet just because the background page still reports board=true.
    func requirePointerOnBoard() async throws { _ = try await observeBoardPointer() }

    func observeBoardPointer(requireStable: Bool = true) async throws -> PointerLocator.Point {
        let after = Date().addingTimeInterval(0.15)
        let deadline = Date().addingTimeInterval(4)
        var previous: PointerLocator.Point?, lastID: String?
        var stableSince: Date?
        while Date() < deadline {
            try Task.checkCancellation()
            if let frame = await frames.latestFrame(), frame.frameID != lastID, frame.capturedAt >= after,
               await frames.ageMilliseconds(of: frame) < 1_000 {
                lastID = frame.frameID
                if let board = ViewportMarkers.locate(jpeg: frame.jpeg),
                   let point = BoardPointerLocator.locate(jpeg: frame.jpeg, board: board) {
                    if !requireStable { return point }
                    if let previous, hypot(point.x - previous.x, point.y - previous.y) < 0.003 {
                        if let stableSince, frame.capturedAt.timeIntervalSince(stableSince) >= 0.12 { return point }
                    } else { stableSince = frame.capturedAt }
                    previous = point
                } else { previous = nil; stableSince = nil }
            }
            try await Task.sleep(for: .milliseconds(25))
        }
        throw PointerControlError.pointerOutsideBoard
    }

    private func waitForTap(since date: Date, timeoutMilliseconds: Int = 3_000) async -> FrameStore.PointerTap? {
        let deadline = Date().addingTimeInterval(Double(timeoutMilliseconds) / 1_000)
        while Date() < deadline {
            if Task.isCancelled { return nil }
            if let tap = await frames.taps(since: date).first { return tap }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return nil
    }

    // MARK: - aiming and tapping

    /// Moves to a screen fraction, optionally closing the loop by finding the pointer first.
    /// Moves to a screen fraction in one burst.
    ///
    /// Calibration and production use the identical burst profile and axis order. Re-homing
    /// keeps external pointer movement from silently accumulating drift.
    public func pointerMove(x: Double, y: Double, aim: Bool = false) async throws {
        try await validateCalibrationScreen()
        guard let span = pointerCalibration.span else { throw PointerControlError.notCalibrated }
        try await pointerHome()
        let unitsX = Int((pointerCalibration.corrected(x, axis: .x) * Double(span.width)).rounded())
        let unitsY = Int((pointerCalibration.corrected(y, axis: .y) * Double(span.height)).rounded())
        try await hid.moveCalibrated(dx: unitsX, dy: unitsY, profile: pointerMotionProfile)
        pointerUnits = (unitsX, unitsY)
        if aim { try await correctPointerAim(x, y) }
    }

    /// Finds the pointer, corrects a miss immediately, and learns from a normal one.
    func correctPointerAim(_ x: Double, _ y: Double) async throws {
        guard let span = pointerCalibration.span else { throw PointerControlError.notCalibrated }
        try await PointerAim.align(target: .init(x: x, y: y), span: span, observe: {
            // A second independent observation is required; ambiguity rejects the tap.
            guard let first = try await self.locatePointer(expectedX: x, expectedY: y),
                  let second = try await self.locatePointer(expectedX: x, expectedY: y),
                  hypot(first.x-second.x, first.y-second.y) <= 0.003 else { return nil }
            return second
        }, move: { dx, dy in
            try await self.sendPointerDelta(dx: dx, dy: dy)
            self.pointerUnits = nil
        })
    }

    /// Every coordinate tap is visually verified before sending a click.
    public func pointerTap(x: Double, y: Double, aim: Bool = false) async throws {
        _ = try await validatedTap(x: x, y: y)
    }

    private func validatedTap(x: Double, y: Double) async throws -> Date {
        try await pointerMove(x: x, y: y, aim: false)
        try await correctPointerAim(x, y)
        let sent = Date()
        try await hid.click(button: "left")
        return sent
    }

    /// The outcome of a tap, reported so a caller can tell a hit from a miss.
    public struct TapOutcome: Sendable {
        public let requested: (x: Double, y: Double)
        /// Where the board says the click landed, when the board was open to say so.
        public let landed: (x: Double, y: Double)?
        public var miss: Double {
            guard let landed else { return 0 }
            return max(abs(landed.x - requested.x), abs(landed.y - requested.y))
        }
    }

    /// Taps a screen fraction and reports what actually happened.
    ///
    /// `pointerTap` can only report the coordinate it aimed at, which is not evidence of a hit:
    /// earlier sessions retried the same spot because the tool said "tapped" whether or not the
    /// click landed. When the calibration board is up it catches the click and the landing is
    /// measured, so the miss is reported and corrected instead of guessed at.
    @discardableResult
    public func tapWithOutcome(x: Double, y: Double, attempts: Int = 2) async throws -> TapOutcome {
        if let rect = try? await calibrationViewport(), pointerCalibration.span != nil,
           (rect.left...(rect.left + rect.width)).contains(x),
           (rect.top...(rect.top + rect.height)).contains(y) {
            guard let tap = try await probeTap(x: x, y: y, attempts: attempts) else {
                // No second blind click when a page report is missing; the first may
                // already have operated browser chrome or an AssistiveTouch control.
                throw PointerControlError.noTaps
            }
            return TapOutcome(requested: (x, y), landed: (tap.x, tap.y))
        }
        try await pointerTap(x: x, y: y)
        return TapOutcome(requested: (x, y), landed: nil)
    }

    /// Board taps arrive as viewport fractions; a caller works in screen fractions.
    func landingInScreenFraction(_ tap: FrameStore.PointerTap) async -> (x: Double, y: Double)? {
        // probeTap already reports the landing in screen fractions, so no conversion is needed
        // here; this exists so callers never have to know which space a tap arrived in.
        return (tap.x, tap.y)
    }

    /// Compatibility alias: all absolute moves share the calibrated transport profile.
    public func pointerJump(x: Double, y: Double) async throws {
        try await pointerMove(x: x, y: y)
    }

    /// Jumps to a screen fraction, then taps. See `pointerJump`.
    public func pointerJumpTap(x: Double, y: Double) async throws {
        try await pointerTap(x: x, y: y)
    }

    /// Taps only after visual convergence, then reports the board's measured landing.
    @discardableResult
    public func probeTap(x: Double, y: Double, attempts: Int = 2) async throws -> FrameStore.PointerTap? {
        guard await frames.isCaptureActive() else { throw ControlFrameError.captureInactive }
        _ = try await calibrationViewport()
        let sent = try await validatedTap(x: x, y: y)
        guard let tap = await waitForTap(since: sent) else { throw PointerControlError.noTaps }
        let viewport = try await calibrationViewport()
        return FrameStore.PointerTap(x: viewport.screenFraction(forViewport: tap.x, axis: .x),
                                     y: viewport.screenFraction(forViewport: tap.y, axis: .y), at: tap.at)
    }

    /// Compatibility alias for a visually verified coordinate tap.
    public func pointerTapLocated(x: Double, y: Double) async throws {
        try await pointerTap(x: x, y: y, aim: true)
    }

    /// Scrolls the screen. Positive numbers move the content up, so a feed advances.
    public func scrollScreen(wheel: Int) async throws {
        guard isConnected else { throw BLEHIDError.notConnected }
        try await hid.scroll(wheel: wheel)
        // Wheel reports do not move the pointer, so the tracked origin stays valid.
    }

    /// Drags the AssistiveTouch menu out of the way.
    ///
    /// When the menu opens over the thing being driven there is no key that closes it, so the
    /// recovery is physical: press, walk to the far corner, release. `dx`/`dy` default to a drag
    /// toward the top-left, which is where the menu is least likely to cover content.
    public func moveAssistiveTouch(dx: Int = -600, dy: Int = -600) async throws {
        guard isConnected else { throw BLEHIDError.notConnected }
        try await hid.dragTo(dx: dx, dy: dy)
        pointerUnits = nil
    }


    // MARK: - full-screen feeds

    /// A real press/move/release gesture. No blind pause/resume taps on unknown controls.
    public func swipeScreen(direction: String) async throws {
        let endpoints: (Double, Double, Double, Double)
        switch direction {
        case "up": endpoints = (0.5, 0.76, 0.5, 0.24)
        case "down": endpoints = (0.5, 0.24, 0.5, 0.76)
        case "left": endpoints = (0.78, 0.5, 0.22, 0.5)
        case "right": endpoints = (0.22, 0.5, 0.78, 0.5)
        default: throw FeedControlError.invalidDirection
        }
        try await pointerMove(x: endpoints.0, y: endpoints.1)
        guard let span = pointerCalibration.span else { throw PointerControlError.notCalibrated }
        let dx = Int(((endpoints.2 - endpoints.0) * Double(span.width)).rounded())
        let dy = Int(((endpoints.3 - endpoints.1) * Double(span.height)).rounded())
        defer { pointerUnits = nil }
        try await hid.swipe(dx: dx, dy: dy, profile: pointerMotionProfile)
    }

    /// One transition per call; the agent must inspect item identity before another.
    /// Wheel fallback is explicit because wheel sensitivity varies by app and device.
    public func advanceFeed(items: Int, wheelPerItem: Int = 120, mode: String = "swipe") async throws {
        guard items == 1 || items == -1 else { throw FeedControlError.oneItemAtATime }
        try await validateCalibrationScreen()
        switch mode {
        case "swipe": try await swipeScreen(direction: items > 0 ? "up" : "down")
        case "wheel":
            guard (1...1200).contains(wheelPerItem) else { throw HIDReportError.movementOutOfRange }
            try await pointerMove(x: 0.5, y: 0.5)
            guard let wheel = FeedDirection.wheelDelta(items: items, amount: wheelPerItem) else { throw FeedControlError.oneItemAtATime }
            try await hid.scroll(wheel: wheel)
        default: throw FeedControlError.invalidMode
        }
    }

    /// A centered single tap. Playback behavior differs across apps; verify the icon/state.
    public func togglePlayback() async throws {
        try await pointerTap(x: 0.5, y: 0.5)
    }

    // MARK: - session preparation

    /// New chats can reuse the same validated model without resetting the foreground app.
    public func ensureSession() async throws -> String {
        guard isConnected else { throw BLEHIDError.notConnected }
        guard await frames.isCaptureActive(), let frame = await frames.latestFrame(),
              await frames.ageMilliseconds(of: frame) <= 5_000 else {
            throw SessionPreparationError.noCapture
        }
        if pointerCalibration.canReuse(forScreenAspect: frameAspect(frame), deviceIdentifier: centralIdentifier) {
            let proof = pointerCalibration.evidence == .heldOut
                ? String(format: "held-out miss %.2f%%", (pointerCalibration.quality ?? 0) * 100)
                : "estimated mapping; independent accuracy not measured"
            return "Session ready: live capture and Bluetooth connected; saved calibration for this phone reused (motion \(pointerMotionProfile.rawValue), \(proof)). Current app preserved. Inspect this screen and continue the user's task."
        }
        return try await prepareSession(timeoutSeconds: 0)
    }

    /// Brings the phone to a known, calibrated, ready-to-drive state in one call.
    ///
    /// Waits for the owner's screen share to arrive, opens the calibration board, measures
    /// the pointer against it, closes the board and preserves Safari, so the next
    /// agent action starts from a clean state instead of asking the user to press anything.
    public func prepareSession(timeoutSeconds: Int = 90) async throws -> String {
        guard isConnected else { throw BLEHIDError.notConnected }

        // 1. Wait for the share the owner started on the iPhone.
        let deadline = Date().addingTimeInterval(Double(timeoutSeconds))
        while Date() < deadline {
            if await frames.isCaptureActive(), await frames.latestFrame() != nil { break }
            try await Task.sleep(for: .milliseconds(500))
        }
        guard await frames.isCaptureActive() else { throw SessionPreparationError.noCapture }

        // 2. Show the board so clicks can be measured, and wait for the page to obey.
        setPageBoard(open: true)
        var boardUp = false
        let boardDeadline = Date().addingTimeInterval(15)
        while Date() < boardDeadline {
            if (try? await calibrationViewport()) != nil { boardUp = true; break }
            try await Task.sleep(for: .milliseconds(300))
        }
        guard boardUp else {
            setPageBoard(open: false)
            throw SessionPreparationError.boardUnavailable
        }

        // 3. Calibrate against the board, then hide it and go home.
        defer { setPageBoard(open: false) }
        var selectedSpan: PointerCalibration.Span?
        var lastProbeError: Error = PointerControlError.notCalibrated
        // Measure the fastest burst profile itself. No calibration from another cadence
        // is reused, and a failed two-click fit is not silently called validated.
        pointerMotionProfile = .fast16
        do {
            selectedSpan = try await quickProbePointer()
        } catch let error as PointerControlError {
            lastProbeError = error
        }
        guard let span = selectedSpan else { throw lastProbeError }
        // Close the board and leave the app alone: the owner gets the sharing controls back
        // and the agent decides what to open next.
        setPageBoard(open: false)

        let quality = pointerCalibration.quality.map { String(format: " validation miss %.2f%%", $0 * 100) } ?? ""
        return "Session ready: motion \(pointerMotionProfile.rawValue), span \(span.width)×\(span.height) units\(quality), two clicks measured; independent accuracy not measured; board closed. Inspect the current screen."
    }

    /// Only a visible board supplies calibration geometry. App screenshots and Safari's
    /// visualViewport are never used to change an already learned screen mapping.
    func calibrationViewport() async throws -> ViewportMarkers.Rect {
        guard await pageBoardIsOpen(), let frame = await frames.latestFrame(),
              await frames.ageMilliseconds(of: frame) <= 5_000,
              let measured = ViewportMarkers.locate(jpeg: frame.jpeg) else {
            throw PointerControlError.boardGeometryUnavailable
        }
        return measured
    }

    func frameAspect(_ frame: ScreenshotFrame) -> Double? {
        guard let source = CGImageSourceCreateWithData(frame.jpeg as CFData, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber, height.doubleValue > 0 else { return nil }
        return width.doubleValue / height.doubleValue
    }

    func validateCalibrationScreen() async throws {
        guard await frames.isCaptureActive(), let fresh = await frames.latestFrame(),
              await frames.ageMilliseconds(of: fresh) <= 5_000 else { throw ControlFrameError.captureInactive }
        guard let device = centralIdentifier, pointerCalibration.deviceIdentifier == device else {
            throw PointerControlError.deviceChanged
        }
        guard pointerCalibration.motionVersion == PointerCalibration.currentMotionVersion,
              pointerCalibration.motionProfile == pointerMotionProfile,
              let saved = pointerCalibration.screenAspectRatio else { throw PointerControlError.notCalibrated }
        guard let frame = await frames.latestFrame(), let current = frameAspect(frame),
              abs(current - saved) < 0.01 else { throw PointerControlError.screenChanged }
    }

    /// Reads the board state the capture page reports with each frame.
    func pageBoardIsOpen() async -> Bool {
        guard let report = await frames.report() else { return false }
        return report.contains("board=true")
    }
}

public enum PointerControlError: Error, LocalizedError {
    case notCalibrated
    case notFound
    case noTaps
    case boardGeometryUnavailable
    case screenChanged
    case deviceChanged
    case inaccurateCalibration
    case quickProbeRejected(String)
    case pointerOutsideBoard

    public var errorDescription: String? {
        switch self {
        case .pointerOutsideBoard: "No unambiguous pointer is visible inside the white board. No click was sent. Keep the board visible; standard grey pointer appearance is required for this safety check."
        case .quickProbeRejected(let details): "Two-click calibration rejected (edge, baseline or offset): " + details
        case .notCalibrated: "The pointer is not calibrated. Run ensure_session first."
        case .notFound: "The pointer could not be found on screen. Keep the screen still and try again."
        case .inaccurateCalibration: "Calibration geometry is inconsistent, clamped or too narrow, or a held-out target missed by over 2.5%. Keep the board visible and run pointer_probe for full validation."
        case .deviceChanged: "Calibration belongs to a different or unverified phone. Run ensure_session with the Safari sharing page visible."
        case .screenChanged: "Screen orientation or geometry changed. Run prepare_session again."
        case .boardGeometryUnavailable: "The calibration board border is not visible. Bring the sharing page to the foreground."
        case .noTaps: "No taps reached the calibration board. Open the board on the iPhone and keep it in front."
        }
    }
}

enum PointerCalibrationProbe {
    static let firstStop = 150
    static let secondStop = 350
    /// Corners and centre, as screen fractions, used by the full diagnostic probe.
    static let targets: [(x: Double, y: Double)] = [(0.25, 0.25), (0.75, 0.25), (0.5, 0.5), (0.25, 0.75), (0.75, 0.75)]

}

public enum SessionPreparationError: Error, LocalizedError {
    case noCapture
    case boardUnavailable

    public var errorDescription: String? {
        switch self {
        case .noCapture: "No screen share is arriving. Start sharing in Safari on the iPhone first, then run prepare_session again."
        case .boardUnavailable: "The calibration board is not visible in the shared screen. Bring the existing Open iPhone Use Safari sharing tab to the foreground, then retry. A background board=true report is insufficient."
        }
    }
}

public enum FeedControlError: Error, LocalizedError {
    case invalidDirection, invalidMode, oneItemAtATime
    public var errorDescription: String? {
        switch self {
        case .invalidDirection: "Swipe direction must be up, down, left or right."
        case .invalidMode: "Feed mode must be swipe or wheel."
        case .oneItemAtATime: "Use dy=1 or -1, then inspect the returned item's account/title before advancing again."
        }
    }
}
