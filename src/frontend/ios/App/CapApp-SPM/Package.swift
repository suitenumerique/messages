// swift-tools-version: 5.9
import PackageDescription

// DO NOT MODIFY THIS FILE - managed by Capacitor CLI commands
let package = Package(
    name: "CapApp-SPM",
    platforms: [.iOS(.v15)],
    products: [
        .library(
            name: "CapApp-SPM",
            targets: ["CapApp-SPM"])
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", exact: "8.5.2"),
        .package(name: "CapacitorApp", path: "../../../node_modules/.store/@capacitor/app@8.1.1-wNt0-Xn68T5jHIT05pHc3A/node_modules/@capacitor/app"),
        .package(name: "CapacitorBrowser", path: "../../../node_modules/.store/@capacitor/browser@8.0.4-K2p5wKgR6RF8RN8M-7sF9Q/node_modules/@capacitor/browser"),
        .package(name: "CapacitorDevice", path: "../../../node_modules/.store/@capacitor/device@8.0.3-TGCF5K-IfQWZkqCvbIvDlA/node_modules/@capacitor/device"),
        .package(name: "CapacitorFilesystem", path: "../../../node_modules/.store/@capacitor/filesystem@8.1.3-NtPfWYqFqkCVCy_ne0dHKw/node_modules/@capacitor/filesystem"),
        .package(name: "CapacitorHaptics", path: "../../../node_modules/.store/@capacitor/haptics@8.0.2-6m-Q7gP5SMidXQvSR8CKbw/node_modules/@capacitor/haptics"),
        .package(name: "CapacitorKeyboard", path: "../../../node_modules/.store/@capacitor/keyboard@8.0.5-V8JXGWRGCR7fscSRVhB3ig/node_modules/@capacitor/keyboard"),
        .package(name: "CapacitorPushNotifications", path: "../../../node_modules/.store/@capacitor/push-notifications@8.1.2-CjmXDIH559TfwU6M8HuN-w/node_modules/@capacitor/push-notifications"),
        .package(name: "CapacitorShare", path: "../../../node_modules/.store/@capacitor/share@8.0.2-bAcnwkOQtWmZJmhTGVI9Mg/node_modules/@capacitor/share"),
        .package(name: "CapgoCapacitorUpdater", path: "../../../node_modules/.store/@capgo/capacitor-updater@8.51.23-KnF62eqm7wa-nSGeGz2qrw/node_modules/@capgo/capacitor-updater")
    ],
    targets: [
        .target(
            name: "CapApp-SPM",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm"),
                .product(name: "Cordova", package: "capacitor-swift-pm"),
                .product(name: "CapacitorApp", package: "CapacitorApp"),
                .product(name: "CapacitorBrowser", package: "CapacitorBrowser"),
                .product(name: "CapacitorDevice", package: "CapacitorDevice"),
                .product(name: "CapacitorFilesystem", package: "CapacitorFilesystem"),
                .product(name: "CapacitorHaptics", package: "CapacitorHaptics"),
                .product(name: "CapacitorKeyboard", package: "CapacitorKeyboard"),
                .product(name: "CapacitorPushNotifications", package: "CapacitorPushNotifications"),
                .product(name: "CapacitorShare", package: "CapacitorShare"),
                .product(name: "CapgoCapacitorUpdater", package: "CapgoCapacitorUpdater")
            ]
        )
    ]
)
