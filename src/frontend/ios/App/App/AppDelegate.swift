import UIKit
import UserNotifications
import Capacitor

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    /// Category every APNs alert carries (APNS_CATEGORY in
    /// core/services/push/apns.py).
    static let newMessageCategory = "NEW_MESSAGE"

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        AppDelegate.excludeWebViewDataFromBackup()
        AppDelegate.registerNotificationCategories()
        return true
    }

    /// When the user's "Show Previews" setting hides notification content
    /// (lock screen, or always), iOS shows this placeholder instead of the
    /// sender and subject the Notification Service Extension filled in — the
    /// generic text rather than a bare "Notification". Merged into the
    /// existing categories: setNotificationCategories replaces the whole set.
    static func registerNotificationCategories() {
        let center = UNUserNotificationCenter.current()
        center.getNotificationCategories { categories in
            let newMessage = UNNotificationCategory(
                identifier: AppDelegate.newMessageCategory,
                actions: [],
                intentIdentifiers: [],
                hiddenPreviewsBodyPlaceholder: NSLocalizedString("NEW_MESSAGE", comment: ""),
                options: []
            )
            center.setNotificationCategories(
                categories.filter { $0.identifier != AppDelegate.newMessageCategory }.union([newMessage])
            )
        }
    }

    /// iOS counterpart of android:allowBackup="false": the WKWebView keeps the
    /// Django session cookie (Library/Cookies) and the native CSRF token in
    /// localStorage (Library/WebKit), which iCloud/Finder backups would
    /// otherwise restore onto another device, transplanting an active session.
    /// Re-applied on every launch and whenever the scene enters background
    /// (SceneDelegate): at first launch these directories do not exist yet
    /// (the WKWebView is created after didFinishLaunching), and WebKit can
    /// later recreate them without the exclusion attribute.
    static func excludeWebViewDataFromBackup() {
        let library = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
        for directory in ["Cookies", "WebKit"] {
            var url = library.appendingPathComponent(directory, isDirectory: true)
            guard FileManager.default.fileExists(atPath: url.path) else { continue }
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? url.setResourceValues(values)
        }
    }

    // Hand the APNs registration outcome to @capacitor/push-notifications:
    // its native side observes these notifications and resolves the JS
    // "registration"/"registrationError" events the app awaits
    // (features/native/push.ts, obtainToken).
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationCenter.default.post(name: .capacitorDidRegisterForRemoteNotifications, object: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NotificationCenter.default.post(name: .capacitorDidFailToRegisterForRemoteNotifications, object: error)
    }

    func application(_ application: UIApplication, configurationForConnecting connectingSceneSession: UISceneSession, options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)
        config.delegateClass = SceneDelegate.self
        return config
    }

}
