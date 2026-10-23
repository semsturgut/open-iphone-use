import Foundation

/// The pinned Swift MCP SDK decodes experimental capabilities as [String: String].
/// Clients may send arbitrary JSON values there. Open iPhone Use implements no experimental
/// capabilities, so omit unsupported values before SDK decoding rather than fail startup.
public enum MCPHandshakeCompatibility {
    public static func normalize(_ data: Data) -> Data {
        guard var message = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              message["method"] as? String == "initialize",
              var params = message["params"] as? [String: Any],
              var capabilities = params["capabilities"] as? [String: Any],
              let experimental = capabilities["experimental"] as? [String: Any],
              experimental.values.contains(where: { !($0 is String) }) else { return data }
        capabilities["experimental"] = experimental.filter { $0.value is String }
        params["capabilities"] = capabilities
        message["params"] = params
        return (try? JSONSerialization.data(withJSONObject: message)) ?? data
    }
}
