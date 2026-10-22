import CryptoKit
import UserNotifications

/**
 * Replaces the generic "New message" alert with the sender and subject sealed
 * for this device (`enc`, `kid` — see docs/push-notifications.md §5.1).
 *
 * Runs for every APNs alert thanks to `mutable-content: 1`, in its own process
 * with a few seconds and a few MB to spare: no network, no WebView — only the
 * Keychain-shared keyring. Any failure (no preview, unknown or rotated-out key,
 * bad ciphertext) delivers the alert untouched, i.e. the generic banner.
 */
final class NotificationService: UNNotificationServiceExtension {
    private struct Preview: Decodable {
        let sender: String
        let subject: String
    }

    /// The containing app's bundle (the extension lives in App.app/PlugIns/):
    /// its Localizable.strings already back the generic APNs alert, so the
    /// extension reads its strings there rather than shipping a copy.
    private static let appBundle: Bundle = {
        let appURL = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return Bundle(url: appURL) ?? .main
    }()

    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var bestAttemptContent: UNMutableNotificationContent?

    override func didReceive(
        _ request: UNNotificationRequest,
        withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void
    ) {
        self.contentHandler = contentHandler
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            contentHandler(request.content)
            return
        }
        bestAttemptContent = content

        let userInfo = content.userInfo
        // Group the banners of one thread together, as the collapse id does
        // for the notification itself.
        if let threadId = userInfo["thread_id"] as? String {
            content.threadIdentifier = "thread-\(threadId)"
        }
        if let preview = Self.openPreview(userInfo) {
            if !preview.sender.isEmpty {
                content.title = preview.sender
            }
            content.body = preview.subject.isEmpty
                ? NSLocalizedString(
                    "NO_SUBJECT", bundle: Self.appBundle, value: "No subject", comment: ""
                )
                : preview.subject
        }
        // iOS badges are always numbers: count the banners still waiting
        // rather than the unread mail of every mailbox (meaningless summed
        // up). This one replaces the banner of its thread, if any (same
        // apns-collapse-id, hence same request identifier). The badge drops
        // to 0 once the app is opened (SceneDelegate).
        UNUserNotificationCenter.current().getDeliveredNotifications { delivered in
            let others = delivered.filter { $0.request.identifier != request.identifier }
            content.badge = NSNumber(value: others.count + 1)
            contentHandler(content)
        }
    }

    override func serviceExtensionTimeWillExpire() {
        if let contentHandler, let bestAttemptContent {
            contentHandler(bestAttemptContent)
        }
    }

    private static func openPreview(_ userInfo: [AnyHashable: Any]) -> Preview? {
        guard let enc = userInfo["enc"] as? String,
              let kid = userInfo["kid"] as? String,
              let record = Data(base64URLEncoded: enc),
              let key = PushPreviewKeyring().key(kid: kid),
              let privateKey = try? P256.KeyAgreement.PrivateKey(rawRepresentation: key.privateKey),
              let plaintext = try? PushPreviewCrypto.decrypt(
                  record, privateKey: privateKey, authSecret: key.authSecret
              )
        else { return nil }
        // Trailing padding spaces are valid JSON whitespace.
        return try? JSONDecoder().decode(Preview.self, from: plaintext)
    }
}
