// Narrow SystemConfiguration adapter. Snapshot data is private; stdout is a pipe to the worker.
import Foundation
import SystemConfiguration
import CryptoKit
import Darwin
import IOKit

func approvedSerial() throws -> String {
    let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    let config = executable.deletingLastPathComponent().appendingPathComponent("lab.local.json")
    let attributes = try FileManager.default.attributesOfItem(atPath: config.path)
    try require(attributes[.type] as? FileAttributeType == .typeRegular,
                "private_lab_config_required")
    let mode = (attributes[.posixPermissions] as? NSNumber)?.uint32Value ?? 0o777
    let owner = (attributes[.ownerAccountID] as? NSNumber)?.uint32Value
    let size = (attributes[.size] as? NSNumber)?.intValue ?? 0
    let executableAttributes = try FileManager.default.attributesOfItem(atPath: executable.path)
    let executableOwner = (executableAttributes[.ownerAccountID] as? NSNumber)?.uint32Value
    try require(mode & 0o077 == 0 && (owner == 0 || owner == getuid() || owner == executableOwner) && size > 0 && size <= 8192,
                "private_lab_config_required")
    let data = try Data(contentsOf: config)
    guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let serial = value["approved_serial"] as? String,
          serial.range(of: "^[a-zA-Z0-9]{8,32}$", options: .regularExpression) != nil else {
        throw Refusal(code: "lab_config_serial_required")
    }
    return serial
}


struct Refusal: Error { let code: String }
func require(_ condition: Bool, _ code: String) throws {
    if !condition { throw Refusal(code: code) }
}
func hardwareSerial() throws -> String {
    let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPlatformExpertDevice"))
    try require(service != 0, "hardware_identity_unavailable")
    defer { IOObjectRelease(service) }
    guard let property = IORegistryEntryCreateCFProperty(service, "IOPlatformSerialNumber" as CFString, kCFAllocatorDefault, 0),
          let serial = property.takeRetainedValue() as? String else {
        throw Refusal(code: "hardware_identity_unavailable")
    }
    try require(serial == approvedSerial(), "managed_device_refused")
    return serial
}
func encoded(_ value: CFDictionary?) throws -> Any {
    guard let value = value else { return NSNull() }
    return try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0).base64EncodedString()
}
func decoded(_ value: Any?) throws -> CFDictionary? {
    if value is NSNull { return nil }
    guard let text = value as? String, let data = Data(base64Encoded: text),
          let dictionary = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? NSDictionary else {
        throw Refusal(code: "invalid_snapshot_configuration")
    }
    return dictionary as CFDictionary
}
func dnsFaultConfiguration(_ captured: CFDictionary?) -> CFDictionary {
    var configuration = captured as? [String: Any] ?? [:]
    configuration["ServerAddresses"] = ["ADDRESS_REMOVED"]
    return configuration as CFDictionary
}
func stable(_ value: Any) throws -> Any {
    if let data = value as? Data { return ["plist_data": data.base64EncodedString()] }
    if let date = value as? Date { return ["plist_date": date.timeIntervalSince1970] }
    if let dictionary = value as? [String: Any] { return try dictionary.mapValues { try stable($0) } }
    if let array = value as? [Any] { return try array.map { try stable($0) } }
    try require(JSONSerialization.isValidJSONObject([value]), "unsupported_property_type")
    return value
}
func hash(_ value: Any) throws -> String {
    let data = try JSONSerialization.data(withJSONObject: stable(value), options: [.sortedKeys])
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}
struct Context {
    let prefs: SCPreferences
    let wifi: SCNetworkService
    let interface: String
    let dns: SCNetworkProtocol
    let proxy: SCNetworkProtocol
    let protectedHash: String
    let locationHash: String
    let serviceOrderHash: String
    init() throws {
        _ = try hardwareSerial()
        guard let prefs = SCPreferencesCreate(nil, "Uplink isolated recovery" as CFString, nil),
              let services = SCNetworkServiceCopyAll(prefs) as? [SCNetworkService] else {
            throw Refusal(code: "service_inventory_unavailable")
        }
        let matches = services.filter { (SCNetworkServiceGetName($0) as String?) == "Wi-Fi" }
        try require(matches.count == 1, "unique_wifi_service_required")
        let wifi = matches[0]
        guard let iface = SCNetworkServiceGetInterface(wifi),
              (SCNetworkInterfaceGetInterfaceType(iface) as String?) == (kSCNetworkInterfaceTypeIEEE80211 as String),
              let bsd = SCNetworkInterfaceGetBSDName(iface) as String?,
              bsd.range(of: "^en[0-9]+$", options: .regularExpression) != nil, bsd != "en7",
              let dns = SCNetworkServiceCopyProtocol(wifi, kSCNetworkProtocolTypeDNS),
              let proxy = SCNetworkServiceCopyProtocol(wifi, kSCNetworkProtocolTypeProxies) else {
            throw Refusal(code: "wifi_interface_or_protocol_refused")
        }
        try require(SCNetworkServiceGetEnabled(wifi), "wifi_service_disabled")
        var protected: [String: Any] = [:]
        for service in services where service !== wifi {
            guard let name = SCNetworkServiceGetName(service) as String? else { continue }
            if name != "AX88179B" && name != "Thunderbolt Bridge" { continue }
            guard let device = SCNetworkServiceGetInterface(service) else { throw Refusal(code: "protected_interface_missing") }
            var protocols: [String: Any] = [:]
            for key in [kSCNetworkProtocolTypeDNS, kSCNetworkProtocolTypeProxies, kSCNetworkProtocolTypeIPv4, kSCNetworkProtocolTypeIPv6] {
                if let proto = SCNetworkServiceCopyProtocol(service, key) {
                    protocols[key as String] = SCNetworkProtocolGetConfiguration(proto) as Any? ?? NSNull()
                }
            }
            protected[name] = ["id": SCNetworkServiceGetServiceID(service) as String? ?? "",
                               "enabled": SCNetworkServiceGetEnabled(service),
                               "interface": SCNetworkInterfaceGetBSDName(device) as String? ?? "",
                               "protocols": protocols]
        }
        try require(protected.count == 2, "both_protected_services_required")
        guard let current = SCNetworkSetCopyCurrent(prefs),
              let location = SCNetworkSetGetSetID(current) as String?,
              let order = SCNetworkSetGetServiceOrder(current) as? [String] else {
            throw Refusal(code: "location_or_service_order_unavailable")
        }
        self.prefs = prefs; self.wifi = wifi; self.interface = bsd
        self.dns = dns; self.proxy = proxy; self.protectedHash = try hash(protected)
        self.locationHash = try hash([location]); self.serviceOrderHash = try hash(order)
    }
    func snapshot() throws -> [String: Any] {
        let proxyConfig = SCNetworkProtocolGetConfiguration(proxy) as? [String: Any] ?? [:]
        let unsafe = proxyConfig.keys.contains { key in
            let k = key.lowercased()
            return k.contains("password") || k.contains("username") || k.contains("authentication")
        }
        let dynamic = SCDynamicStoreCopyProxies(nil) as? [String: Any] ?? [:]
        return ["version": 1, "serial": try approvedSerial(),
                "service_id": SCNetworkServiceGetServiceID(wifi) as String? ?? "",
                "interface": interface, "dns": try encoded(SCNetworkProtocolGetConfiguration(dns)),
                "proxies": try encoded(SCNetworkProtocolGetConfiguration(proxy)),
                "protected_sha256": protectedHash,
                "location_sha256": locationHash, "service_order_sha256": serviceOrderHash,
                "dns_sha256": try hash([SCNetworkProtocolGetConfiguration(dns) as Any? ?? NSNull()]),
                "proxies_sha256": try hash([SCNetworkProtocolGetConfiguration(proxy) as Any? ?? NSNull()]),
                "policy_safe": !unsafe && ["ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable", "SOCKSEnable"].allSatisfy { ((proxyConfig[$0] as? NSNumber)?.intValue ?? 0) == 0 },
                "effective_proxy": dynamic]
    }
}
func main() throws {
    let args = CommandLine.arguments
    try require(args.count >= 2, "operation_required")
    let operation = args[1]
    if operation == "identity" {
        try require(args.count == 2, "identity_takes_no_arguments")
        let data = try JSONSerialization.data(withJSONObject: ["serial": hardwareSerial()], options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        return
    }
    if operation == "self-test" {
        try require(args.count == 2, "self_test_takes_no_arguments")
        let original: NSDictionary = ["HTTPEnable": 0, "HTTPProxy": "", "HTTPPort": 0,
                                     "HTTPSEnable": 0, "HTTPSProxy": "", "HTTPSPort": 0]
        let restored = try decoded(encoded(original as CFDictionary))
        try require((restored as NSDictionary?)?.isEqual(original) == true, "disabled_proxy_roundtrip_failed")
        try require(try decoded(NSNull()) == nil, "absent_configuration_roundtrip_failed")
        let dnsBaseline: [String: Any] = ["ServerAddresses": ["ADDRESS_REMOVED"],
                                        "SearchDomains": ["example.test"],
                                        "SupplementalMatchDomains": ["internal.test"], "SearchOrder": 42]
        var expectedDNS = dnsBaseline
        expectedDNS["ServerAddresses"] = ["ADDRESS_REMOVED"]
        let faultedDNS = dnsFaultConfiguration(dnsBaseline as CFDictionary) as NSDictionary
        try require(faultedDNS.isEqual(expectedDNS as NSDictionary), "dns_fault_changed_other_settings")
        let emptyDNS = dnsFaultConfiguration(nil) as NSDictionary
        try require(emptyDNS.isEqual(["ServerAddresses": ["ADDRESS_REMOVED"]]), "absent_dns_fault_failed")
        let first = try hash(["a": [1, 2], "b": "value"])
        let second = try hash(["b": "value", "a": [1, 2]])
        try require(first == second, "canonical_configuration_digest_failed")
        FileHandle.standardOutput.write(Data("{\"self_test\":\"passed\",\"network_reads\":0,\"network_writes\":0}".utf8))
        return
    }
    let context = try Context()
    if operation == "read" {
        try require(args.count == 2, "read_takes_no_arguments")
        let data = try JSONSerialization.data(withJSONObject: context.snapshot(), options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        return
    }
    try require(geteuid() == 0 && hardwareSerial() == approvedSerial(), "mutation_requires_root_lab")
    try require(args.count == 3 && ["wrong_dns", "stale_proxy", "dns_and_proxy", "set_dns", "restore_proxy", "restore"].contains(operation), "unsupported_native_operation")
    let path = args[2]
    try require(path.range(of: "^/var/db/uplink-real/runs/[0-9a-f]{12}/snapshot.json$", options: .regularExpression) != nil, "root_snapshot_path_required")
    let attributes = try FileManager.default.attributesOfItem(atPath: path)
    try require((attributes[.ownerAccountID] as? NSNumber)?.intValue == 0 && (attributes[.posixPermissions] as? NSNumber)?.intValue == 384 && attributes[.type] as? FileAttributeType == .typeRegular, "private_root_snapshot_required")
    guard let snapshot = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any] else { throw Refusal(code: "invalid_snapshot") }
    try require(snapshot["service_id"] as? String == (SCNetworkServiceGetServiceID(context.wifi) as String?) && snapshot["interface"] as? String == context.interface, "wifi_identity_changed")
    try require(snapshot["protected_sha256"] as? String == context.protectedHash, "protected_services_changed")
    try require(snapshot["serial"] as? String == approvedSerial() &&
                snapshot["location_sha256"] as? String == context.locationHash &&
                snapshot["service_order_sha256"] as? String == context.serviceOrderHash,
                "device_location_or_service_order_changed")
    try require(SCPreferencesLock(context.prefs, false), "preferences_busy_no_retry")
    defer { SCPreferencesUnlock(context.prefs) }
    // One preferences transaction, only the existing Wi-Fi DNS and Proxies entities.
    if ["wrong_dns", "dns_and_proxy", "set_dns", "restore"].contains(operation) {
        var config = try decoded(snapshot["dns"])
        if ["wrong_dns", "dns_and_proxy"].contains(operation) {
            config = dnsFaultConfiguration(config)
        }
        try require(SCNetworkProtocolSetConfiguration(context.dns, config), "dns_configuration_refused")
    }
    if ["stale_proxy", "dns_and_proxy", "restore_proxy", "restore"].contains(operation) {
        var config = (try decoded(snapshot["proxies"])) as? [String: Any] ?? [:]
        if ["stale_proxy", "dns_and_proxy"].contains(operation) {
            config["HTTPEnable"] = 1; config["HTTPSEnable"] = 1
            config["HTTPProxy"] = "ADDRESS_REMOVED"; config["HTTPSProxy"] = "ADDRESS_REMOVED"
            config["HTTPPort"] = 9; config["HTTPSPort"] = 9
            try require(SCNetworkProtocolSetConfiguration(context.proxy, config as CFDictionary), "proxy_configuration_refused")
        } else {
            try require(SCNetworkProtocolSetConfiguration(context.proxy, try decoded(snapshot["proxies"])), "proxy_restore_refused")
        }
    }
    try require(SCPreferencesCommitChanges(context.prefs), "preferences_commit_failed")
    try require(SCPreferencesApplyChanges(context.prefs), "preferences_apply_failed")
    FileHandle.standardOutput.write(Data("{\"ok\":true}".utf8))
}
do { try main() }
catch let error as Refusal { FileHandle.standardError.write(Data((error.code + "\n").utf8)); exit(2) }
catch { FileHandle.standardError.write(Data("native_operation_failed\n".utf8)); exit(2) }
