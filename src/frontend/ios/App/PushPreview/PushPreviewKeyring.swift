import CryptoKit
import Foundation
import Security

/**
 * The key pairs notification previews are sealed for, shared between the app
 * (which rotates them and registers the current public key) and the
 * Notification Service Extension (which only reads them to decrypt).
 *
 * Rotation contract (docs/push-notifications.md §5.1): a new pair every
 * `rotationInterval`; the previous private key is kept `gracePeriod` longer —
 * longer than the backend's PUSH_TTL_SECONDS (1 day), after which no push
 * sealed for it can still arrive. The keyring belongs to one account: another
 * account registering on the device starts from scratch.
 *
 * Stored as one Keychain item in an access group shared with the extension,
 * `AfterFirstUnlockThisDeviceOnly`: readable while the phone is locked (pushes
 * arrive then), never synced to iCloud nor restored from a backup.
 */
final class PushPreviewKeyring {
    struct Key: Codable {
        let privateKey: Data
        let authSecret: Data
        let createdAt: Date
        var retiredAt: Date?

        var kid: String {
            (try? P256.KeyAgreement.PrivateKey(rawRepresentation: privateKey))
                .map { PushPreviewCrypto.keyId(publicKey: $0.publicKey) } ?? ""
        }
    }

    struct Ring: Codable {
        var userId: String
        /// Newest first; only the first one may be current (not retired).
        var keys: [Key]
    }

    struct PublicKeys {
        let p256dh: String
        let auth: String
    }

    static let rotationInterval: TimeInterval = 30 * 24 * 3600
    static let gracePeriod: TimeInterval = 2 * 24 * 3600

    private static let service = "push-preview-keyring"
    private static let account = "keyring"

    private let accessGroup: String?

    /// `accessGroup` comes from the `PushPreviewKeychainGroup` Info.plist key
    /// (both targets), expanded at build time with the team prefix.
    init(accessGroup: String? = Bundle.main.object(forInfoDictionaryKey: "PushPreviewKeychainGroup") as? String) {
        self.accessGroup = accessGroup
    }

    /// Rotates if due and returns the public half to register for `userId`.
    func currentPublicKeys(userId: String, now: Date = Date()) throws -> PublicKeys {
        let stored = load()
        // Another account's keys are dropped outright, grace period included.
        var keys = (stored?.userId == userId ? stored?.keys ?? [] : []).filter { key in
            guard let retiredAt = key.retiredAt else { return true }
            return now.timeIntervalSince(retiredAt) < Self.gracePeriod
        }
        let currentIsFresh = keys.first.map {
            $0.retiredAt == nil && now.timeIntervalSince($0.createdAt) < Self.rotationInterval
        } ?? false
        if !currentIsFresh {
            keys = keys.map { key in
                var retired = key
                retired.retiredAt = retired.retiredAt ?? now
                return retired
            }
            var authSecret = Data(count: 16)
            let status = authSecret.withUnsafeMutableBytes {
                SecRandomCopyBytes(kSecRandomDefault, 16, $0.baseAddress!)
            }
            guard status == errSecSuccess else { throw KeychainError(status: status) }
            keys.insert(
                Key(
                    privateKey: P256.KeyAgreement.PrivateKey().rawRepresentation,
                    authSecret: authSecret,
                    createdAt: now
                ),
                at: 0
            )
        }
        try save(Ring(userId: userId, keys: keys))

        let current = keys[0]
        let privateKey = try P256.KeyAgreement.PrivateKey(rawRepresentation: current.privateKey)
        return PublicKeys(
            p256dh: privateKey.publicKey.x963Representation.base64URLEncoded(),
            auth: current.authSecret.base64URLEncoded()
        )
    }

    /// The key a preview was sealed for, if still held (current or in grace).
    func key(kid: String) -> Key? {
        load()?.keys.first { $0.kid == kid }
    }

    func clear() {
        SecItemDelete(baseQuery() as CFDictionary)
    }

    // MARK: - Keychain

    struct KeychainError: Error {
        let status: OSStatus
    }

    private func baseQuery() -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
        if let accessGroup {
            query[kSecAttrAccessGroup as String] = accessGroup
        }
        return query
    }

    private func load() -> Ring? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return try? JSONDecoder().decode(Ring.self, from: data)
    }

    private func save(_ ring: Ring) throws {
        let data = try JSONEncoder().encode(ring)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        var status = SecItemUpdate(baseQuery() as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(baseQuery().merging(attributes) { $1 } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }
}
