import Capacitor
import UIKit

/**
 * Applies a change of `Keyboard.setAccessoryBarVisible` to the keyboard being
 * shown. That call only replaces what `inputAccessoryView` returns, which UIKit
 * reads when the keyboard opens, while the web app can only tell which field
 * took the focus once the keyboard is already opening (features/native/keyboard.ts).
 */
@objc(KeyboardAccessoryPlugin)
public class KeyboardAccessoryPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "KeyboardAccessoryPlugin"
    public let jsName = "KeyboardAccessory"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "reload", returnType: CAPPluginReturnPromise),
    ]

    @objc func reload(_ call: CAPPluginCall) {
        DispatchQueue.main.async { [weak self] in
            if let webView = self?.webView {
                Self.firstResponder(in: webView)?.reloadInputViews()
            }
            call.resolve()
        }
    }

    private static func firstResponder(in view: UIView) -> UIView? {
        if view.isFirstResponder { return view }
        for subview in view.subviews {
            if let responder = firstResponder(in: subview) { return responder }
        }
        return nil
    }
}
