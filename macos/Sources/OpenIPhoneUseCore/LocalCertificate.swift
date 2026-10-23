import CoreFoundation
import Foundation
import Security

public enum LocalCertificateError: Error, LocalizedError {
    case noPrivateIPv4
    case openssl(String)
    case identityMissing
    case securityFramework(String)

    public var errorDescription: String? {
        switch self {
        case .noPrivateIPv4: "No active private IPv4 address was found on this Mac's Wi-Fi interface."
        case .openssl(let message): "Could not create the local HTTPS certificate: \(message)"
        case .identityMissing: "The local TLS certificate identity could not be loaded."
        case .securityFramework(let message): "Could not create the local TLS identity: \(message)"
        }
    }
}

public struct LocalTLSIdentity: @unchecked Sendable {
    public let identity: SecIdentity
    public let rootCertificateDER: Data
    public let serverAddress: String
}

public enum LocalCertificateManager {
    public static var rootCertificateURL: URL {
        certificateDirectory.appendingPathComponent("root-ca.cer")
    }

    private static var certificateDirectory: URL {
        OpenIPhoneUsePaths.supportDirectory.appendingPathComponent("certificates", isDirectory: true)
    }

    public static func makeIdentity(for privateIPv4: String) throws -> LocalTLSIdentity {
        let directory = certificateDirectory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        _ = chmod(directory.path, mode_t(S_IRWXU))
        let rootKey = directory.appendingPathComponent("root-ca.key")
        let rootPEM = directory.appendingPathComponent("root-ca.pem")
        let rootDER = directory.appendingPathComponent("root-ca.cer")
        let serverKey = directory.appendingPathComponent("server.key")
        let serverCSR = directory.appendingPathComponent("server.csr")
        let serverCert = directory.appendingPathComponent("server.pem")
        let serverKeyDER = directory.appendingPathComponent("server-key.der")
        let serverCertDER = directory.appendingPathComponent("server-cert.der")
        let extensions = directory.appendingPathComponent("server-extensions.cnf")
        let legacyPKCS12 = directory.appendingPathComponent("server-identity.p12")
        if FileManager.default.fileExists(atPath: legacyPKCS12.path) {
            try FileManager.default.removeItem(at: legacyPKCS12)
        }

        if !FileManager.default.fileExists(atPath: rootPEM.path) {
            try runOpenSSL([
                "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "3650",
                "-keyout", rootKey.path, "-out", rootPEM.path, "-subj", "/CN=Open iPhone Use Local Root CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ])
        }

        try runOpenSSL(["genrsa", "-out", serverKey.path, "2048"])
        try runOpenSSL(["req", "-new", "-key", serverKey.path, "-out", serverCSR.path,
                        "-subj", "/CN=Open iPhone Use Local Capture"])
        let extensionText = """
        basicConstraints=critical,CA:FALSE
        keyUsage=critical,digitalSignature,keyEncipherment
        extendedKeyUsage=serverAuth
        subjectAltName=IP:\(privateIPv4),DNS:open-iphone-use.local
        """
        try extensionText.write(to: extensions, atomically: true, encoding: .utf8)
        try runOpenSSL(["x509", "-req", "-in", serverCSR.path, "-CA", rootPEM.path,
                        "-CAkey", rootKey.path, "-CAcreateserial", "-out", serverCert.path,
                        "-days", "365", "-sha256", "-extfile", extensions.path])
        try runOpenSSL(["rsa", "-in", serverKey.path, "-outform", "DER", "-out", serverKeyDER.path])
        try runOpenSSL(["x509", "-in", serverCert.path, "-outform", "DER", "-out", serverCertDER.path])
        try runOpenSSL(["x509", "-in", rootPEM.path, "-outform", "DER", "-out", rootDER.path])
        for file in [rootKey, serverKey, serverCSR, serverCert, serverKeyDER, serverCertDER, extensions, rootPEM, rootDER] {
            _ = chmod(file.path, mode_t(S_IRUSR | S_IWUSR))
        }

        let keyData = try Data(contentsOf: serverKeyDER)
        let certificateData = try Data(contentsOf: serverCertDER)
        var keyError: Unmanaged<CFError>?
        guard let privateKey = SecKeyCreateWithData(
            keyData as CFData,
            [kSecAttrKeyType: kSecAttrKeyTypeRSA,
             kSecAttrKeyClass: kSecAttrKeyClassPrivate,
             kSecAttrKeySizeInBits: 2048] as CFDictionary,
            &keyError
        ) else {
            throw LocalCertificateError.securityFramework(keyError?.takeRetainedValue().localizedDescription ?? "The private key is invalid.")
        }
        guard let certificate = SecCertificateCreateWithData(nil, certificateData as CFData),
              let identity = SecIdentityCreate(nil, certificate, privateKey) else {
            throw LocalCertificateError.identityMissing
        }
        return LocalTLSIdentity(identity: identity, rootCertificateDER: try Data(contentsOf: rootDER), serverAddress: privateIPv4)
    }

    private static func runOpenSSL(_ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
        process.arguments = arguments
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        do { try process.run() }
        catch { throw LocalCertificateError.openssl(error.localizedDescription) }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let data = output.fileHandleForReading.readDataToEndOfFile()
            throw LocalCertificateError.openssl(String(decoding: data, as: UTF8.self))
        }
    }
}
