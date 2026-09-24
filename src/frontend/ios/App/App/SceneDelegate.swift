import UIKit
import Capacitor
import UserNotifications

class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }

        window = UIWindow(windowScene: windowScene)
        // Not the bare CAPBridgeViewController the `cap migrate-uiscene`
        // template writes: MainViewController registers the app-local plugins
        // (WebAuthSession), without which the native login rejects.
        window?.rootViewController = MainViewController()
        window?.makeKeyAndVisible()

        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        SceneDelegateProxy.shared.scene(scene, openURLContexts: URLContexts)
    }

    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }

    // Under the UIScene lifecycle UIKit no longer calls the AppDelegate's
    // applicationDidBecomeActive / applicationDidEnterBackground: the scene
    // receives these transitions instead.

    func sceneDidBecomeActive(_ scene: UIScene) {
        // The push badge carries the unread count as of send time; once the
        // user is in the app it is stale, so drop it (web counterpart:
        // clearAppBadge in features/auth). Delivered banners are dismissed by
        // the JS side (clearDeliveredNativeNotifications).
        if #available(iOS 16.0, *) {
            UNUserNotificationCenter.current().setBadgeCount(0)
        } else {
            UIApplication.shared.applicationIconBadgeNumber = 0
        }
    }

    func sceneDidEnterBackground(_ scene: UIScene) {
        // Re-apply here so the WebKit/Cookies directories created during the
        // first session are excluded before any backup runs (backups typically
        // trigger while the app is backgrounded).
        AppDelegate.excludeWebViewDataFromBackup()
    }
}
