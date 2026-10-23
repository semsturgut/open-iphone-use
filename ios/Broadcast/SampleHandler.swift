import ReplayKit
import CoreImage
import ImageIO
import UIKit

final class SampleHandler: RPBroadcastSampleHandler {
    private var store: CaptureStore?
    private var configuration = CaptureConfiguration()
    private var status = CaptureStatus()
    private var lastCapture: TimeInterval = 0
    private var inFlight = false
    private var currentUpload: URLSessionUploadTask?
    private var heartbeat: DispatchSourceTimer?
    private var generation = UUID()
    private let lock = NSRecursiveLock()
    private let context = CIContext(options: [.cacheIntermediates: false])
    private lazy var network: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 15
        config.httpCookieStorage = nil
        config.urlCache = nil
        return URLSession(configuration: config, delegate: CaptureRedirectBlocker(), delegateQueue: nil)
    }()
    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        lock.lock(); defer { lock.unlock() }
        do {
            let shared = try CaptureStore()
            guard let saved = shared.load("configuration.json", as: CaptureConfiguration.self) else { throw CaptureError.message("Önce OpenIPhoneUse'da ayarları kaydet.") }
            if saved.upload { _ = try saved.endpoint() }
            shared.resetStop(); shared.clearPreview(); store = shared; configuration = saved
            generation = UUID(); lastCapture = 0; inFlight = false
            status = CaptureStatus(active: true, message: saved.upload ? "Mac’e native ekran yayını gönderiliyor." : "Yerel deneme çalışıyor.")
            persist()
            startHeartbeat()
        } catch { finishBroadcastWithError(error) }
    }
    override func broadcastPaused() {
        lock.lock(); defer { lock.unlock() }
        generation = UUID(); currentUpload?.cancel(); currentUpload = nil; inFlight = false
        status.paused = true; status.message = "iOS yayını duraklattı."; persist()
        notifyMacStop(reason: "paused")
    }
    override func broadcastResumed() { lock.lock(); defer { lock.unlock() }; lastCapture = 0; status.paused = false; status.message = "Yayın devam ediyor."; persist() }
    override func broadcastFinished() {
        lock.lock(); defer { lock.unlock() }
        generation = UUID(); status.active = false; status.paused = false; status.message = "Yayın durdu."; persist()
        heartbeat?.cancel(); heartbeat = nil
        network.invalidateAndCancel()
        currentUpload = nil
        notifyMacStop(reason: "stopped")
    }
    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video else { return }
        lock.lock(); defer { lock.unlock() }
        guard status.active, !status.paused else { return }
        if store?.stopRequested() == true {
            status.active = false; status.message = "Durdurma isteği alındı."; persist()
            finishBroadcastWithError(CaptureError.message("Open iPhone Use yayını durdurdu.")); return
        }
        let now = ProcessInfo.processInfo.systemUptime
        let interval: TimeInterval = 1
        guard now - lastCapture >= interval, !inFlight else { return }
        lastCapture = now
        autoreleasepool {
            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
            var image = CIImage(cvPixelBuffer: pixelBuffer)
            if let attachment = CMGetAttachment(sampleBuffer, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber,
               let orientation = CGImagePropertyOrientation(rawValue: attachment.uint32Value) { image = image.oriented(orientation) }
            let scale = min(1, 1024 / max(image.extent.width, image.extent.height))
            image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            guard let cg = context.createCGImage(image, from: image.extent),
                  let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.65), jpeg.count <= 512 * 1024 else {
                status.message = "Kare dönüştürülemedi veya çok büyük."; persist(); return
            }
            status.captured += 1; status.width = cg.width; status.height = cg.height
            if !configuration.upload {
                do { try jpeg.write(to: store!.directory.appendingPathComponent("latest.jpg"), options: [.atomic, .completeFileProtection]); status.message = "Son kare yalnızca bu iPhone'da." }
                catch { status.message = "Kare kaydedilemedi; cihaz kilitlenmiş olabilir." }
                persist(); return
            }
            guard let request = try? configuration.frameRequest() else { return }
            inFlight = true; let run = generation
            let task = network.uploadTask(with: request, from: jpeg) { [weak self] _, response, error in
                guard let self else { return }
                self.lock.lock(); defer { self.lock.unlock() }
                guard run == self.generation, self.status.active else { return }
                self.inFlight = false
                self.currentUpload = nil
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                if (200..<300).contains(code) { self.status.uploaded += 1; self.status.message = "Kare alıcı tarafından kabul edildi." }
                else { self.status.message = error.map(captureNetworkFailure) ?? "Alıcı isteği reddetti (\(code))." }
                self.persist()
                if [401, 404, 409].contains(code) { self.status.active = false; self.persist(); self.finishBroadcastWithError(CaptureError.message("Oturum sona erdi veya yayın anahtarı geçersiz.")) }
            }
            currentUpload = task
            task.resume()
            persist()
        }
    }
    private func notifyMacStop(reason: String) {
        guard let request = try? configuration.stopRequest(reason: reason) else { return }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 2; config.timeoutIntervalForResource = 3
        config.httpCookieStorage = nil; config.urlCache = nil
        let client = URLSession(configuration: config, delegate: CaptureRedirectBlocker(), delegateQueue: nil)
        client.dataTask(with: request) { _, _, _ in client.finishTasksAndInvalidate() }.resume()
    }
    private func startHeartbeat() {
        heartbeat?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "io.openiphoneuse.broadcast.heartbeat"))
        timer.schedule(deadline: .now() + 2, repeating: 2)
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            self.lock.lock(); defer { self.lock.unlock() }
            guard self.status.active else { return }
            if self.store?.stopRequested() == true {
                self.status.active = false; self.status.message = "Durdurma isteği alındı."; self.persist()
                self.heartbeat?.cancel(); self.heartbeat = nil
                self.finishBroadcastWithError(CaptureError.message("Open iPhone Use yayını durdurdu."))
            } else { self.persist() }
        }
        heartbeat = timer
        timer.resume()
    }
    private func persist() { status.updatedAt = Date(); try? store?.save(status, as: "status.json") }
}
