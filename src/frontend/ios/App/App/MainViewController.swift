import Capacitor
import UIKit

/**
 * Bridge view controller registering the app-local plugins: plugins living
 * in the app target (not in a package) are not auto-discovered by Capacitor.
 */
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(WebAuthSessionPlugin())
    }
}
