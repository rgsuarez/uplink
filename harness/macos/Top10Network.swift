// TOP10 snapshot-only adapter. No secret is printed except private configuration
// bytes on the worker's pipe. Keychain reads and radio/rejoin/profile mutations are forbidden.
import Foundation
import SystemConfiguration
import CoreWLAN
import CryptoKit
import Darwin
import IOKit

struct Refusal: Error { let code: String }
func require(_ condition: Bool, _ code: String) throws {
    if !condition { throw Refusal(code: code) }
}
let heldOperations = ["can_restore_network", "radio_off", "restore_radio", "forget_current_profile", "restore_saved_network"]
func enforceOperationPolicy(_ operation: String, effectiveUID _: uid_t) throws {
    // Deliberately independent of privilege. Root never earns a credential-read exception.
    try require(!heldOperations.contains(operation), "top10_keychain_rejoin_profile_operation_held")
}
func encoded(_ value: CFDictionary?) throws -> Any {
    guard let value = value else { return NSNull() }
    return try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0).base64EncodedString()
}
func decoded(_ value: Any?) throws -> CFDictionary? {
    if value is NSNull { return nil }
    guard let text = value as? String, let data = Data(base64Encoded: text),
          let dictionary = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? NSDictionary else {
        throw Refusal(code: "top10_invalid_snapshot_configuration")
    }
    return dictionary as CFDictionary
}
func stable(_ value: Any) throws -> Any {
    if let data = value as? Data { return ["plist_data": data.base64EncodedString()] }
    if let date = value as? Date { return ["plist_date": date.timeIntervalSince1970] }
    if let dictionary = value as? [String: Any] { return try dictionary.mapValues { try stable($0) } }
    if let array = value as? [Any] { return try array.map { try stable($0) } }
    try require(JSONSerialization.isValidJSONObject([value]), "top10_unsupported_property_type")
    return value
}
func hash(_ value: Any) throws -> String {
    let data = try JSONSerialization.data(withJSONObject: stable(value), options: [.sortedKeys])
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}
func approvedSerial() throws -> String {
    let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    let config = executable.deletingLastPathComponent().appendingPathComponent("lab.local.json")
    let attributes = try FileManager.default.attributesOfItem(atPath: config.path)
    let owner = (attributes[.ownerAccountID] as? NSNumber)?.uint32Value
    let mode = (attributes[.posixPermissions] as? NSNumber)?.uint32Value ?? 0o777
    try require(attributes[.type] as? FileAttributeType == .typeRegular && mode & 0o077 == 0 &&
                (owner == 0 || owner == getuid()) &&
                ((attributes[.size] as? NSNumber)?.intValue ?? 0) <= 8192, "top10_private_config_required")
    let value = try JSONSerialization.jsonObject(with: Data(contentsOf: config)) as? [String: Any]
    guard let serial = value?["approved_serial"] as? String,
          serial.range(of: "^[a-zA-Z0-9]{8,32}$", options: .regularExpression) != nil else {
        throw Refusal(code: "top10_serial_binding_required")
    }
    return serial
}
func hardwareSerial() throws -> String {
    let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPlatformExpertDevice"))
    try require(service != 0, "top10_hardware_identity_unavailable")
    defer { IOObjectRelease(service) }
    guard let property = IORegistryEntryCreateCFProperty(service, "IOPlatformSerialNumber" as CFString, kCFAllocatorDefault, 0),
          let serial = property.takeRetainedValue() as? String else {
        throw Refusal(code: "top10_hardware_identity_unavailable")
    }
    try require(serial == approvedSerial(), "top10_wrong_device")
    return serial
}
func profileRows(_ configuration: CWConfiguration) -> [[String: Any]] {
    (configuration.networkProfiles.array as? [CWNetworkProfile] ?? []).map {
        ["ssid_data": $0.ssidData?.base64EncodedString() ?? "", "security": $0.security.rawValue]
    }
}
func profileState(_ configuration: CWConfiguration) -> [String: Any] {
    ["profiles": profileRows(configuration),
     "remember_joined": configuration.rememberJoinedNetworks,
     "require_association_admin": configuration.requireAdministratorForAssociation,
     "require_power_admin": configuration.requireAdministratorForPower,
     "require_ibss_admin": configuration.requireAdministratorForIBSSMode]
}
func decodeCW(_ snapshot: [String: Any]) throws -> CWConfiguration {
    guard let text = snapshot["cw_configuration"] as? String, let data = Data(base64Encoded: text),
          let value = try NSKeyedUnarchiver.unarchivedObject(ofClass: CWConfiguration.self, from: data) else {
        throw Refusal(code: "top10_saved_profiles_unavailable")
    }
    return value
}
func dnsFault(_ captured: CFDictionary?) -> CFDictionary {
    var config = captured as? [String: Any] ?? [:]
    config["ServerAddresses"] = ["ADDRESS_REMOVED"]
    return config as CFDictionary
}
func manualFault(_ captured: CFDictionary?, dynamic: CFDictionary?, stale: Bool) throws -> CFDictionary {
    var config = captured as? [String: Any] ?? [:]
    let active = dynamic as? [String: Any] ?? [:]
    guard let addresses = active["Addresses"] as? [String], addresses.count == 1,
          let masks = active["SubnetMasks"] as? [String], masks.count == 1 else {
        throw Refusal(code: "top10_unique_baseline_ipv4_required")
    }
    config["ConfigMethod"] = "Manual"
    config["Addresses"] = stale ? ["ADDRESS_REMOVED"] : addresses
    config["SubnetMasks"] = stale ? ["ADDRESS_REMOVED"] : masks
    config["Router"] = stale ? "ADDRESS_REMOVED" : "ADDRESS_REMOVED"
    return config as CFDictionary
}
struct Context {
    let prefs: SCPreferences
    let service: SCNetworkService
    let interface: String
    let wifi: CWInterface
    let protocols: [String: SCNetworkProtocol]
    let protectedHash: String
    let locationHash: String
    let orderHash: String
    init() throws {
        _ = try hardwareSerial()
        guard let prefs = SCPreferencesCreate(nil, "Uplink TOP10" as CFString, nil),
              let services = SCNetworkServiceCopyAll(prefs) as? [SCNetworkService] else {
            throw Refusal(code: "top10_services_unavailable")
        }
        let matches = services.filter { service in
            guard let iface = SCNetworkServiceGetInterface(service) else { return false }
            return (SCNetworkInterfaceGetInterfaceType(iface) as String?) == (kSCNetworkInterfaceTypeIEEE80211 as String)
        }
        try require(matches.count == 1, "top10_unique_wifi_required")
        let service = matches[0]
        guard let iface = SCNetworkServiceGetInterface(service),
              let bsd = SCNetworkInterfaceGetBSDName(iface) as String?,
              bsd.range(of: "^en[0-9]+$", options: .regularExpression) != nil,
              let wifi = CWWiFiClient.shared().interface(withName: bsd) else {
            throw Refusal(code: "top10_wifi_interface_unavailable")
        }
        try require(SCNetworkServiceGetEnabled(service), "top10_wifi_service_disabled")
        var protocols: [String: SCNetworkProtocol] = [:]
        for name in [kSCNetworkProtocolTypeDNS, kSCNetworkProtocolTypeProxies, kSCNetworkProtocolTypeIPv4, kSCNetworkProtocolTypeIPv6] {
            guard let proto = SCNetworkServiceCopyProtocol(service, name) else {
                throw Refusal(code: "top10_wifi_protocol_missing")
            }
            protocols[name as String] = proto
        }
        var protected: [String: Any] = [:]
        for other in services where (SCNetworkServiceGetServiceID(other) as String?) != (SCNetworkServiceGetServiceID(service) as String?) {
            guard let id = SCNetworkServiceGetServiceID(other) as String?,
                  let device = SCNetworkServiceGetInterface(other) else { continue }
            var values: [String: Any] = [:]
            for name in [kSCNetworkProtocolTypeDNS, kSCNetworkProtocolTypeProxies, kSCNetworkProtocolTypeIPv4, kSCNetworkProtocolTypeIPv6] {
                if let proto = SCNetworkServiceCopyProtocol(other, name) {
                    values[name as String] = SCNetworkProtocolGetConfiguration(proto) as Any? ?? NSNull()
                }
            }
            protected[id] = ["enabled": SCNetworkServiceGetEnabled(other),
                             "interface": SCNetworkInterfaceGetBSDName(device) as String? ?? "", "protocols": values]
        }
        try require(protected.count >= 2, "top10_protected_services_missing")
        guard let current = SCNetworkSetCopyCurrent(prefs),
              let location = SCNetworkSetGetSetID(current) as String?,
              let order = SCNetworkSetGetServiceOrder(current) as? [String] else {
            throw Refusal(code: "top10_location_unavailable")
        }
        self.prefs = prefs; self.service = service; self.interface = bsd; self.wifi = wifi
        self.protocols = protocols; self.protectedHash = try hash(protected)
        self.locationHash = try hash([location]); self.orderHash = try hash(order)
    }
    func configuration(_ name: String) -> CFDictionary? {
        SCNetworkProtocolGetConfiguration(protocols[name]!)
    }
    func dynamic(_ name: String) -> CFDictionary? {
        guard let store = SCDynamicStoreCreate(nil, "Uplink TOP10 read" as CFString, nil, nil),
              let id = SCNetworkServiceGetServiceID(service) as String? else { return nil }
        guard let value = SCDynamicStoreCopyValue(store, "State:/Network/Service/\(id)/\(name)" as CFString) as? NSDictionary else { return nil }
        return value as CFDictionary
    }
    func snapshot() throws -> [String: Any] {
        guard let cw = wifi.configuration() else { throw Refusal(code: "top10_profiles_unavailable") }
        let proxy = configuration("Proxies") as? [String: Any] ?? [:]
        let unsafe = proxy.keys.contains { key in
            let name = key.lowercased()
            return name.contains("password") || name.contains("username") || name.contains("authentication")
        }
        var result: [String: Any] = ["version": 1, "serial": try approvedSerial(),
            "service_id": SCNetworkServiceGetServiceID(service) as String? ?? "",
            "interface": interface, "protected_sha256": protectedHash,
            "location_sha256": locationHash, "service_order_sha256": orderHash,
            "radio_on": wifi.powerOn(), "ssid_data": wifi.ssidData()?.base64EncodedString() as Any? ?? NSNull(),
            "bssid": wifi.bssid() as Any? ?? NSNull(), "security": wifi.security().rawValue,
            "cw_configuration": try NSKeyedArchiver.archivedData(withRootObject: cw, requiringSecureCoding: true).base64EncodedString(),
            "cw_sha256": try hash(profileState(cw)), "profiles": profileRows(cw),
            "dynamic_ipv4": try encoded(dynamic("IPv4")), "dynamic_dns": try encoded(dynamic("DNS")),
            "effective_proxy": SCDynamicStoreCopyProxies(nil) as Any? ?? [:],
            "policy_safe": !unsafe && ["ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable", "SOCKSEnable"].allSatisfy { ((proxy[$0] as? NSNumber)?.intValue ?? 0) == 0 }]
        for name in ["DNS", "Proxies", "IPv4", "IPv6"] {
            result[name.lowercased()] = try encoded(configuration(name))
            result[name.lowercased() + "_sha256"] = try hash([configuration(name) as Any? ?? NSNull()])
        }
        return result
    }
    func validate(_ saved: [String: Any]) throws {
        try require(saved["serial"] as? String == approvedSerial() &&
                    saved["interface"] as? String == interface &&
                    saved["service_id"] as? String == (SCNetworkServiceGetServiceID(service) as String?), "top10_snapshot_identity_changed")
        try require(saved["protected_sha256"] as? String == protectedHash &&
                    saved["location_sha256"] as? String == locationHash &&
                    saved["service_order_sha256"] as? String == orderHash, "top10_protected_configuration_changed")
        try require(try hash([configuration("IPv6") as Any? ?? NSNull()]) == saved["ipv6_sha256"] as? String,
                    "top10_unowned_ipv6_configuration_changed")
    }
    func setProtocols(_ values: [String: CFDictionary?]) throws {
        try require(SCPreferencesLock(prefs, false), "top10_preferences_busy_no_retry")
        defer { SCPreferencesUnlock(prefs) }
        for (name, value) in values {
            try require(SCNetworkProtocolSetConfiguration(protocols[name]!, value), "top10_protocol_write_refused")
        }
        try require(SCPreferencesCommitChanges(prefs), "top10_preferences_commit_failed")
        try require(SCPreferencesApplyChanges(prefs), "top10_preferences_apply_failed")
    }
}
func main() throws {
    let args = CommandLine.arguments
    try require(args.count >= 2, "top10_operation_required")
    let op = args[1]
    if op == "self-test" {
        try require(args.count == 2, "top10_self_test_no_arguments")
        let original: NSDictionary = ["SearchDomains": ["example.test"], "ServerAddresses": ["ADDRESS_REMOVED"]]
        let originalData = try encoded(original as CFDictionary)
        try require((try decoded(originalData) as NSDictionary?)?.isEqual(original) == true, "top10_plist_roundtrip_failed")
        let changed = dnsFault(original as CFDictionary) as NSDictionary
        try require(changed["SearchDomains"] as? [String] == ["example.test"], "top10_dns_preservation_failed")
        let manual = try manualFault(["ConfigMethod": "DHCP", "DHCPClientID": "fixture"] as CFDictionary,
                                    dynamic: ["Addresses": ["ADDRESS_REMOVED"], "SubnetMasks": ["ADDRESS_REMOVED"]] as CFDictionary, stale: false) as NSDictionary
        try require(manual["Addresses"] as? [String] == ["ADDRESS_REMOVED"] && manual["DHCPClientID"] as? String == "fixture", "top10_manual_preservation_failed")
        for uid: uid_t in [0, 501] {
            for operation in heldOperations {
                do {
                    try enforceOperationPolicy(operation, effectiveUID: uid)
                    throw Refusal(code: "top10_held_guard_did_not_refuse")
                } catch let refusal as Refusal {
                    try require(refusal.code == "top10_keychain_rejoin_profile_operation_held", "top10_held_guard_failed")
                }
            }
        }
        print("{\"self_test\":\"passed\",\"network_reads\":0,\"network_writes\":0,\"keychain_reads\":0,\"keychain_guard\":\"root_and_user_refused\",\"held_paths_checked\":10}")
        return
    }
    if op == "identity" {
        try require(args.count == 2, "top10_identity_no_arguments")
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["serial": hardwareSerial()]))
        return
    }
    try enforceOperationPolicy(op, effectiveUID: geteuid())
    let context = try Context()
    if op == "read" {
        try require(args.count == 2, "top10_read_no_arguments")
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: context.snapshot(), options: [.sortedKeys]))
        return
    }
    let operations = ["can_restore_network", "radio_off", "restore_radio", "bad_dns", "restore_dns", "wrong_gateway", "stale_ipv4", "restore_ipv4", "stale_proxy", "portal_proxy", "restore_proxy", "forget_current_profile", "restore_saved_network", "restore_configuration", "verify_profiles"]
    try require(geteuid() == 0 && operations.contains(op) && (args.count == 3 || (op == "portal_proxy" && args.count == 4)), "top10_root_snapshot_operation_required")
    let path = args[2]
    try require(path.range(of: "^/private/var/db/uplink-top10/runs/[0-9a-f]{12}/snapshot.json$", options: .regularExpression) != nil, "top10_root_snapshot_path_required")
    let attributes = try FileManager.default.attributesOfItem(atPath: path)
    try require((attributes[.ownerAccountID] as? NSNumber)?.intValue == 0 &&
                (attributes[.posixPermissions] as? NSNumber)?.intValue == 384 &&
                attributes[.type] as? FileAttributeType == .typeRegular, "top10_private_root_snapshot_required")
    guard let outer = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any],
          let saved = outer["native"] as? [String: Any] else { throw Refusal(code: "top10_snapshot_invalid") }
    try context.validate(saved)
    switch op {
    case "can_restore_network", "radio_off", "restore_radio", "forget_current_profile", "restore_saved_network":
        throw Refusal(code: "top10_keychain_rejoin_profile_operation_held")
    case "bad_dns": try context.setProtocols(["DNS": dnsFault(try decoded(saved["dns"]))])
    case "restore_dns": try context.setProtocols(["DNS": try decoded(saved["dns"])])
    case "wrong_gateway", "stale_ipv4":
        try context.setProtocols(["IPv4": manualFault(try decoded(saved["ipv4"]), dynamic: try decoded(saved["dynamic_ipv4"]), stale: op == "stale_ipv4")])
    case "restore_ipv4": try context.setProtocols(["IPv4": try decoded(saved["ipv4"])])
    case "stale_proxy", "portal_proxy":
        let port = op == "stale_proxy" ? 9 : Int(args[3]) ?? 0
        try require(port == 9 || (1024...65535).contains(port), "top10_owned_proxy_port_required")
        var config = try decoded(saved["proxies"]) as? [String: Any] ?? [:]
        config["HTTPEnable"] = 1; config["HTTPSEnable"] = 1
        config["HTTPProxy"] = "ADDRESS_REMOVED"; config["HTTPSProxy"] = "ADDRESS_REMOVED"
        config["HTTPPort"] = port; config["HTTPSPort"] = port
        try context.setProtocols(["Proxies": config as CFDictionary])
    case "restore_proxy": try context.setProtocols(["Proxies": try decoded(saved["proxies"])])
    case "restore_configuration":
        try context.setProtocols(["DNS": try decoded(saved["dns"]), "Proxies": try decoded(saved["proxies"]), "IPv4": try decoded(saved["ipv4"])])
    case "verify_profiles":
        guard let current = context.wifi.configuration() else { throw Refusal(code: "top10_profiles_unavailable") }
        try require(current.isEqual(to: decodeCW(saved)), "top10_profile_configuration_not_restored")
    default: throw Refusal(code: "top10_operation_refused")
    }
    print("{\"ok\":true}")
}
do { try main() }
catch let error as Refusal { FileHandle.standardError.write(Data((error.code + "\n").utf8)); exit(2) }
catch { FileHandle.standardError.write(Data("top10_native_operation_failed\n".utf8)); exit(2) }
