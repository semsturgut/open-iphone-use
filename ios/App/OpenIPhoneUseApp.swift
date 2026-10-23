import SwiftUI
import ReplayKit
import UIKit

@main struct OpenIPhoneUseApp: App {
    var body: some Scene { WindowGroup { CaptureView() } }
}
struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let view = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 64, height: 64))
        view.preferredExtension = Bundle.main.bundleIdentifier! + ".broadcast"
        view.showsMicrophoneButton = false
        view.accessibilityLabel = "Open iPhone Use ekran paylaşımını başlat"
        return view
    }
    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
struct CaptureView: View {
    @State private var configuration = CaptureConfiguration()
    @State private var status = CaptureStatus()
    @State private var feedback = ""
    @State private var preview: UIImage?
    @State private var loaded = false
    @State private var prepared = false
    @State private var checkingReceiver = false
    @State private var statusCheckedAt = Date()
    private var broadcastIsLive: Bool { status.hasLiveHeartbeat(at: statusCheckedAt) }
    private var broadcastIsUncertain: Bool { status.active && !broadcastIsLive }
    @Environment(\.scenePhase) private var scenePhase
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    private let navy = Color(red: 0.07, green: 0.13, blue: 0.22)
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack { Text("OPEN IPHONE USE").font(.caption.bold()).tracking(3); Spacer(); Text("iOS · CAPTURE").font(.caption.monospaced()) }
                Text("Ekranın,\nsenin kontrolünde.").font(.system(size: 38, weight: .bold, design: .rounded))
                Text("Ekranı uygulamalar arasında paylaş. Görüntüyü aynı Wi-Fi'daki Open iPhone Use Mac'e gönder.")
                    .foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    step("iphone", "GÖRÜNTÜ"); Image(systemName: "arrow.right"); step("desktopcomputer", "MAC + AGENT"); Image(systemName: "arrow.right"); step("cursorarrow", "AKSİYON")
                }.font(.caption).padding(16).background(.white, in: RoundedRectangle(cornerRadius: 20))
                VStack(alignment: .leading, spacing: 12) {
                    Label(broadcastIsUncertain ? "Yayın durumu güncel değil" : (broadcastIsLive ? (status.paused ? "Yayın duraklatıldı" : "Yayın devam ediyor") : "Yayına hazırla"), systemImage: broadcastIsLive ? "record.circle.fill" : "record.circle")
                        .font(.title3.bold()).foregroundStyle(broadcastIsLive ? Color.red : navy)
                    Text(status.message).font(.subheadline)
                    if broadcastIsUncertain { Text("Yayın uzantısından güncel durum alınamıyor. Ayarları yeniden kaydedebilirsin. iOS yayın göstergesi hâlâ açıksa önce oradan durdur; ardından yeni yayın başlat.").font(.footnote).foregroundStyle(.orange) }
                    HStack { metric("Kare", status.captured); metric("Gönderilen", status.uploaded); Spacer(); Text("\(status.width) × \(status.height)").font(.caption.monospaced()) }
                }.padding(20).background(.white, in: RoundedRectangle(cornerRadius: 20))
                VStack(alignment: .leading, spacing: 14) {
                    Text("1  Mac’e bağlan").font(.headline)
                    Toggle("Ekran karelerini gönder", isOn: $configuration.upload)
                    Text(configuration.upload ? "Bu native yayın Safari'ye bağlı değildir. Diğer uygulamalara geçebilirsin; iOS yayını durdurana veya duraklatana kadar görüntü gönderilir." : "Yerel deneme: yalnızca son ekran karesi bu iPhone'da tutulur; internete gönderilmez.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if configuration.upload {
                        SecureField("Mac’teki HTTPS paylaşım bağlantısı", text: $configuration.macCaptureURL)
                            .keyboardType(.URL)
                        Text("Mac OpenIPhoneUse’daki bağlantıyı eksiksiz yapıştır. Aynı Wi-Fi’da olmalısın; yerel CA sertifikasına iPhone’da güven vermelisin.")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button(checkingReceiver ? "Bağlantı kontrol ediliyor…" : "Alıcı bağlantısını kontrol et") { Task { await checkReceiver() } }.disabled(checkingReceiver)
                    }
                    Button("Ayarları kaydet") { save() }.buttonStyle(.borderedProminent).tint(navy).foregroundStyle(.white)
                }.textInputAutocapitalization(.never).autocorrectionDisabled().disabled(broadcastIsLive)
                VStack(alignment: .leading, spacing: 12) {
                    Text("2  Paylaşımı sen başlat").font(.headline)
                    Text("Ayarları kaydettikten sonra yayın simgesine dokun, OpenIPhoneUse'ı seç ve iOS'un ekran yayını isteğini onayla.").font(.subheadline)
                    if prepared { BroadcastPicker().frame(width: 64, height: 64).background(.white, in: Circle()) }
                    Text("Bildirimler ve kişisel bilgiler de görüntüye girebilir. Mikrofon ve ses kaydı kullanılmaz. Kilitli veya korumalı ekranlarda kontrol varsayılmaz.").font(.footnote).foregroundStyle(.secondary)
                    if status.active { Button("Durdurma isteği gönder", role: .destructive) { perform { try CaptureStore().requestStop() }; feedback = "İstek gönderildi; iOS yayın göstergesinden de durdurabilirsin." } }
                }
                if !feedback.isEmpty { Text(feedback).font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("feedback") }
                if !broadcastIsLive, let preview {
                    VStack(alignment: .leading) {
                        Text("Son yerel kare").font(.headline)
                        Image(uiImage: preview).resizable().scaledToFit().frame(maxHeight: 320).clipShape(RoundedRectangle(cornerRadius: 16))
                    }
                }
                Button("Yerel kareyi ve yayın anahtarını temizle", role: .destructive) {
                    perform {
                        let store = try CaptureStore()
                        store.clearPreview(); configuration.macCaptureURL = ""; try store.save(configuration, as: "configuration.json"); preview = nil; prepared = false
                    }
                }.font(.footnote).disabled(broadcastIsLive)
                Text("Mac Open iPhone Use penceresinde canlı görüntüyü izle. iPhone'da yayını sen başlatır ve durdurursun.").font(.caption).foregroundStyle(.secondary)
            }.padding(24)
        }.background(Color(red: 0.96, green: 0.95, blue: 0.91)).foregroundStyle(navy)
            .onAppear { if !loaded { configuration = (try? CaptureStore().load("configuration.json", as: CaptureConfiguration.self)) ?? CaptureConfiguration(); loaded = true }; refresh() }
            .onReceive(timer) { _ in if scenePhase == .active { refresh() } }
            .task {
                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("--check-receiver") { await checkReceiver() }
                #endif
            }
            .onChange(of: configuration.upload) { _, _ in prepared = false }
            .onChange(of: configuration.macCaptureURL) { _, _ in prepared = false }
    }
    private func step(_ symbol: String, _ label: String) -> some View { VStack(spacing: 8) { Image(systemName: symbol).font(.title2); Text(label).font(.system(size: 9, weight: .bold)).tracking(1) }.frame(maxWidth: .infinity) }
    private func metric(_ label: String, _ value: Int) -> some View { VStack(alignment: .leading) { Text("\(value)").font(.title2.monospacedDigit().bold()); Text(label).font(.caption) }.frame(minWidth: 75, alignment: .leading) }
    private func perform(_ operation: () throws -> Void) { do { try operation() } catch { feedback = error.localizedDescription } }
    private func save() {
        perform {
            if configuration.upload { _ = try configuration.endpoint() }
            try CaptureStore().save(configuration, as: "configuration.json")
            prepared = true; feedback = configuration.upload ? "Alıcı ayarları kaydedildi. Yayını başlatabilirsin." : "Yerel deneme hazır. Yayını başlatabilirsin."
        }
    }
    @MainActor private func checkReceiver() async {
        checkingReceiver = true
        defer { checkingReceiver = false }
        do {
            let request = try configuration.healthRequest()
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 10; config.timeoutIntervalForResource = 15
            config.httpCookieStorage = nil; config.urlCache = nil; config.waitsForConnectivity = true
            let client = URLSession(configuration: config, delegate: CaptureRedirectBlocker(), delegateQueue: nil)
            defer { client.finishTasksAndInvalidate() }
            let (_, response) = try await client.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw CaptureError.message("Alıcı yanıtı beklenen durumda değil.") }
            feedback = "Alıcı bağlantısı doğrulandı. Ayarları kaydedip yayını başlatabilirsin."
            try? CaptureStore().save(ReceiverCheck(succeeded: true, message: feedback), as: "receiver-check.json")
        } catch {
            feedback = captureNetworkFailure(error)
            try? CaptureStore().save(ReceiverCheck(succeeded: false, message: feedback), as: "receiver-check.json")
        }
    }
    private func refresh() {
        statusCheckedAt = Date()
        guard let store = try? CaptureStore() else { feedback = "App Group erişimi yok. Geliştirme imzası ve App Group ayarını kontrol et."; return }
        status = store.load("status.json", as: CaptureStatus.self) ?? CaptureStatus()
        if !status.active, let data = try? Data(contentsOf: store.directory.appendingPathComponent("latest.jpg")) { preview = UIImage(data: data) }
    }
}
