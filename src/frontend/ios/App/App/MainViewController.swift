import Capacitor
import UIKit

/**
 * Bridge view controller registering the app-local plugins: plugins living
 * in the app target (not in a package) are not auto-discovered by Capacitor.
 *
 * It also owns the keyboard resize of the web view, in place of
 * @capacitor/keyboard (configured with `resize: "none"`): the plugin's native
 * mode snaps the web view to its new size ~200 ms after the keyboard has slid
 * in (capacitor-keyboard#79). Here the web view is resized as soon as the
 * keyboard starts moving. Not animated along with it: WebKit lays the page
 * out once at the final size anyway, an animated frame only slides a strip of
 * background under the already settled content.
 */
class MainViewController: CAPBridgeViewController {
    /// Height of the window covered by the docked keyboard (0 when hidden).
    private var keyboardOverlap: CGFloat = 0
    private var contentOffsetObservation: NSKeyValueObservation?

    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(WebAuthSessionPlugin())
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(keyboardWillChangeFrame(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification,
            object: nil
        )
        // When the keyboard shows, the web view's root scroll view is still
        // offset down by the keyboard height, then scrolled back to 0 along
        // with the keyboard animation (UIKit's automatic keyboard avoidance,
        // which the plugin does not switch off): the whole page drops, then
        // slides back up. Every route lays out in a full-height shell with its
        // own inner scrollers, so the root scroll view never has to move: pin
        // it while the keyboard is up. KVO leaves Capacitor's scroll delegate
        // (zoom lock) in place.
        contentOffsetObservation = webView?.scrollView.observe(\.contentOffset) { [weak self] scrollView, _ in
            guard let self, self.keyboardOverlap > 0 else { return }
            self.resetRootScrollOffset(scrollView)
        }
    }

    override func viewWillTransition(to size: CGSize, with coordinator: UIViewControllerTransitionCoordinator) {
        super.viewWillTransition(to: size, with: coordinator)
        // The window resets its root view (the web view) to its full bounds
        // on rotation / resize, dropping the keyboard inset.
        coordinator.animate(alongsideTransition: nil) { [weak self] _ in
            self?.applyKeyboardOverlap()
        }
    }

    @objc private func keyboardWillChangeFrame(_ notification: Notification) {
        guard
            let window = view.window,
            let endFrame = notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect
        else { return }

        // The keyboard frame is in screen coordinates: converting it into the
        // window also covers Stage Manager / Slide Over windows. An undocked
        // (floating or split iPad) keyboard does not reach the window bottom
        // and floats over the content, so it must not shrink the page.
        let keyboardFrame = window.convert(endFrame, from: window.screen.coordinateSpace)
        let isDocked = keyboardFrame.maxY >= window.bounds.maxY
        let overlap = isDocked ? max(0, window.bounds.maxY - keyboardFrame.minY) : 0
        guard overlap != keyboardOverlap else { return }
        keyboardOverlap = overlap
        applyKeyboardOverlap()
        // The avoidance offset may have been applied before this handler ran.
        if overlap > 0, let scrollView = webView?.scrollView {
            resetRootScrollOffset(scrollView)
        }
    }

    private func resetRootScrollOffset(_ scrollView: UIScrollView) {
        if scrollView.contentOffset != .zero {
            scrollView.contentOffset = .zero
        }
    }

    private func applyKeyboardOverlap() {
        guard let window = view.window, let webView = webView else { return }
        let bounds = window.bounds
        webView.frame = CGRect(
            x: bounds.minX,
            y: bounds.minY,
            width: bounds.width,
            height: bounds.height - keyboardOverlap
        )
    }
}
