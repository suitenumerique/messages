import Capacitor
import Foundation

/**
 * Hands the web app the public key notification previews must be sealed for
 * (features/native/push.ts registers it with the device), rotating it when
 * due, and wipes the keyring on a voluntary logout / device sign-out.
 * The private keys never leave the Keychain (see PushPreviewKeyring).
 */
@objc(PushPreviewKeysPlugin)
public class PushPreviewKeysPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PushPreviewKeysPlugin"
    public let jsName = "PushPreviewKeys"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "getKeys", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clear", returnType: CAPPluginReturnPromise),
    ]

    /// Keychain items outlive an app deletion: without this, a reinstall (or
    /// another person reinstalling on a handed-over phone) would inherit the
    /// previous installation's keys.
    private static let installedMarker = "pushPreviewKeyringInstalled"

    private let keyring = PushPreviewKeyring()

    override public func load() {
        let defaults = UserDefaults.standard
        if !defaults.bool(forKey: Self.installedMarker) {
            keyring.clear()
            defaults.set(true, forKey: Self.installedMarker)
        }
    }

    @objc func getKeys(_ call: CAPPluginCall) {
        guard let userId = call.getString("userId"), !userId.isEmpty else {
            call.reject("userId is required")
            return
        }
        do {
            let keys = try keyring.currentPublicKeys(userId: userId)
            call.resolve(["p256dh": keys.p256dh, "auth": keys.auth])
        } catch {
            call.reject("Unable to provide the notification preview keys")
        }
    }

    @objc func clear(_ call: CAPPluginCall) {
        keyring.clear()
        call.resolve()
    }
}
