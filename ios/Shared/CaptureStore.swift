import Foundation

struct CaptureConfiguration: Codable {
    var upload = false
    var macCaptureURL = ""
    init() {}
    private enum CodingKeys: String, CodingKey { case upload, macCaptureURL }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        macCaptureURL = try values.decodeIfPresent(String.self, forKey: .macCaptureURL) ?? ""
        // An incomplete saved setup must not start sending automatically.
        upload = try values.decodeIfPresent(Bool.self, forKey: .upload) == true && !macCaptureURL.isEmpty
    }
    func endpoint() throws -> URL { try macConnection(path: "/api/frame").url }

    func frameRequest() throws -> URLRequest {
        let connection = try macConnection(path: "/api/frame")
        var request = URLRequest(url: connection.url)
        request.httpMethod = "POST"
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue(connection.token, forHTTPHeaderField: "X-Open-iPhone-Use-Token")
        return request
    }

    func healthRequest() throws -> URLRequest {
        let connection = try macConnection(path: "/api/status")
        var request = URLRequest(url: connection.url)
        request.setValue(connection.token, forHTTPHeaderField: "X-Open-iPhone-Use-Token")
        return request
    }

    func stopRequest(reason: String) throws -> URLRequest? {
        guard upload else { return nil }
        let connection = try macConnection(path: "/api/stop")
        var request = URLRequest(url: connection.url)
        request.httpMethod = "POST"
        request.setValue(connection.token, forHTTPHeaderField: "X-Open-iPhone-Use-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["reason": reason])
        return request
    }

    private func macConnection(path: String) throws -> (url: URL, token: String) {
        guard var components = URLComponents(string: macCaptureURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme == "https", let host = components.host,
              components.user == nil, components.password == nil, components.fragment == nil,
              components.port == 27890, components.path == "/capture",
              Self.isPrivateIPv4(host), let items = components.queryItems,
              items.count == 1, items[0].name == "token", let token = items[0].value,
              token.count == 64, token.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }) else {
            throw CaptureError.message("Mac OpenIPhoneUse'daki HTTPS paylaşım bağlantısını eksiksiz yapıştır. Aynı Wi-Fi'daki özel IPv4 adresi gerekli.")
        }
        components.path = path
        components.query = nil
        guard let url = components.url else { throw CaptureError.message("Mac bağlantısı geçersiz.") }
        return (url, token)
    }

    private static func isPrivateIPv4(_ host: String) -> Bool {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4, parts.allSatisfy({ !$0.isEmpty && $0.allSatisfy({ $0.isASCII && $0.isNumber }) }),
              parts.allSatisfy({ $0.count == 1 || !$0.hasPrefix("0") }) else { return false }
        let values = parts.compactMap { Int($0) }
        guard values.count == 4, values.allSatisfy({ (0...255).contains($0) }) else { return false }
        return values[0] == 10 || values[0] == 192 && values[1] == 168 || values[0] == 172 && (16...31).contains(values[1])
    }
}
struct CaptureStatus: Codable {
    var active = false
    var paused = false
    var captured = 0
    var uploaded = 0
    var width = 0
    var height = 0
    var message = "Ekran paylaşımı henüz başlamadı."
    var updatedAt = Date()
    // A saved active flag is not proof that the extension is still running.
    func hasLiveHeartbeat(at now: Date = Date()) -> Bool {
        let age = now.timeIntervalSince(updatedAt)
        return active && age >= -5 && age <= 15
    }
}
enum CaptureError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}
struct CaptureStore {
    let directory: URL
    init() throws {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "OpenIPhoneUseAppGroup") as? String,
              let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else {
            throw CaptureError.message("App Group kullanılamıyor. Xcode'da iki hedef için aynı App Group'u ve geliştirme takımını ayarlayın.")
        }
        directory = url
    }
    func load<T: Decodable>(_ name: String, as type: T.Type) -> T? {
        guard let data = try? Data(contentsOf: directory.appendingPathComponent(name)) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
    func save<T: Encodable>(_ value: T, as name: String) throws {
        try JSONEncoder().encode(value).write(to: directory.appendingPathComponent(name), options: [.atomic, .completeFileProtection])
    }
    func clearPreview() { try? FileManager.default.removeItem(at: directory.appendingPathComponent("latest.jpg")) }
    func stopRequested() -> Bool { FileManager.default.fileExists(atPath: directory.appendingPathComponent("stop").path) }
    func requestStop() throws { try Data().write(to: directory.appendingPathComponent("stop"), options: .atomic) }
    func resetStop() { try? FileManager.default.removeItem(at: directory.appendingPathComponent("stop")) }
}

// Never forward a session credential to a redirect destination.
struct ReceiverCheck: Codable {
    var succeeded: Bool
    var message: String
    var updatedAt = Date()
}

// Persist only diagnostic codes, never request headers, URLs, or NSError.userInfo.
func captureNetworkFailure(_ error: Error) -> String {
    let failure = error as NSError
    let detail: String
    switch (failure.domain, failure.code) {
    case (NSURLErrorDomain, -1200 ... -1199): detail = "HTTPS bağlantısı kurulamadı."
    case (NSURLErrorDomain, -1206 ... -1201): detail = "HTTPS sertifikası doğrulanamadı. iPhone'da Open iPhone Use CA profilini ve Sertifika Güven Ayarları'ndaki tam güveni kontrol et."
    case (NSURLErrorDomain, NSURLErrorNotConnectedToInternet): detail = "Ağ erişimi yok. Aynı Wi-Fi bağlantısını ve Open iPhone Use Yerel Ağ iznini kontrol et."
    case (NSURLErrorDomain, NSURLErrorTimedOut): detail = "Alıcı yanıtı zaman aşımına uğradı. Mac'in açık ve alıcının çalışıyor olduğunu kontrol et."
    default: detail = "Alıcı bağlantısı kurulamadı. Wi-Fi, Yerel Ağ izni ve HTTPS sertifika güvenini kontrol et."
    }
    return "\(detail) [\(failure.domain):\(failure.code)]"
}

final class CaptureRedirectBlocker: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
