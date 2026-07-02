import AuthenticationServices
import Capacitor

/**
 * Opens an ASWebAuthenticationSession so the OIDC flow runs in the system
 * browser context and resolves with the deep link the backend redirects to.
 *
 * The default Capacitor Browser plugin uses SFSafariViewController, whose
 * cookie store is isolated from Safari since iOS 11: the identity provider
 * session cookie would not be shared across apps and cross-app SSO would
 * break. ASWebAuthenticationSession shares Safari's persistent cookies.
 *
 * Losing this file (iOS project regeneration) or flipping the ephemeral flag
 * breaks cross-app SSO with NO error — login itself keeps working. Guarded by
 * src/features/native/sso-invariants.test.ts and the manual release checklist
 * in docs/mobile.md.
 */
@objc(WebAuthSessionPlugin)
public class WebAuthSessionPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "WebAuthSessionPlugin"
    public let jsName = "WebAuthSession"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise)
    ]

    private var session: ASWebAuthenticationSession?
    private let presentationContextProvider = PresentationAnchorProvider()

    @objc func start(_ call: CAPPluginCall) {
        guard
            let urlString = call.getString("url"),
            let url = URL(string: urlString),
            let callbackScheme = call.getString("callbackScheme")
        else {
            call.reject("url and callbackScheme are required")
            return
        }

        DispatchQueue.main.async {
            // Overlapping calls (e.g. a double tap on the login button) must
            // not overwrite self.session: the first completion handler would
            // drop the only strong reference to the newer, still-presented
            // session and kill it with no error surfaced to JS.
            if self.session != nil {
                call.reject("An authentication session is already in progress")
                return
            }

            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: callbackScheme
            ) { [weak self] callbackURL, error in
                if let callbackURL {
                    call.resolve(["callbackUrl": callbackURL.absoluteString])
                } else {
                    call.reject(error?.localizedDescription ?? "Authentication was cancelled")
                }
                self?.session = nil
            }
            session.presentationContextProvider = self.presentationContextProvider
            // Sharing persistent cookies with Safari is what provides the
            // cross-app SSO: never switch this session to ephemeral mode.
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            // If presentation fails the completion handler never runs; the
            // session must be released here or every later login would be
            // rejected as "already in progress".
            if !session.start() {
                self.session = nil
                call.reject("Failed to start the authentication session")
            }
        }
    }
}

private class PresentationAnchorProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        ASPresentationAnchor()
    }
}
