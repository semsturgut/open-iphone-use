import Darwin
import Foundation
import Network

public struct PrivateIPv4Interface: Sendable {
    public let address: String
    public let netmask: UInt32

    public func contains(_ candidate: String) -> Bool {
        var local = in_addr()
        var remote = in_addr()
        guard inet_pton(AF_INET, address, &local) == 1,
              inet_pton(AF_INET, candidate, &remote) == 1,
              Self.isPrivate(remote.s_addr) else { return false }
        return (local.s_addr & netmask) == (remote.s_addr & netmask)
    }

    public static func activeWiFi() -> PrivateIPv4Interface? {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return nil }
        defer { freeifaddrs(head) }
        var current: UnsafeMutablePointer<ifaddrs>? = first
        while let item = current {
            defer { current = item.pointee.ifa_next }
            guard let namePointer = item.pointee.ifa_name,
                  String(cString: namePointer).hasPrefix("en"),
                  item.pointee.ifa_addr?.pointee.sa_family == sa_family_t(AF_INET),
                  item.pointee.ifa_flags & UInt32(IFF_UP) != 0,
                  let addressPointer = item.pointee.ifa_addr,
                  let maskPointer = item.pointee.ifa_netmask else { continue }
            let address = addressPointer.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr }
            let mask = maskPointer.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr.s_addr }
            var addressCopy = address
            var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            guard inet_ntop(AF_INET, &addressCopy, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil else { continue }
            let printable = String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
            guard isPrivate(address.s_addr) else { continue }
            return PrivateIPv4Interface(address: printable, netmask: mask)
        }
        return nil
    }

    private static func isPrivate(_ address: in_addr_t) -> Bool {
        let host = UInt32(bigEndian: address)
        return (host & 0xFF00_0000) == 0x0A00_0000
            || (host & 0xFFF0_0000) == 0xAC10_0000
            || (host & 0xFFFF_0000) == 0xC0A8_0000
    }
}

extension NWEndpoint.Host {
    var privateIPv4Address: String? {
        let raw = String(describing: self).trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        var address = in_addr()
        guard inet_pton(AF_INET, raw, &address) == 1 else { return nil }
        return raw
    }
}
