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
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", exact: "8.4.0"),
        .package(name: "CapacitorApp", path: "../../../node_modules/.store/@capacitor/app@8.1.1-NWN4Ed-aygQxAfsrEgK9dw/node_modules/@capacitor/app"),
        .package(name: "CapacitorBrowser", path: "../../../node_modules/.store/@capacitor/browser@8.0.4-z1YPnF4WwgAEOuCvL4-S4w/node_modules/@capacitor/browser"),
        .package(name: "CapacitorDevice", path: "../../../node_modules/.store/@capacitor/device@8.0.3-p34cctI1EwodBVEqrd2D3g/node_modules/@capacitor/device"),
        .package(name: "CapacitorFilesystem", path: "../../../node_modules/.store/@capacitor/filesystem@8.1.2-8AGa1F8-qQiUj9CvPVuBVA/node_modules/@capacitor/filesystem"),
        .package(name: "CapacitorPushNotifications", path: "../../../node_modules/.store/@capacitor/push-notifications@8.1.2-zTQz8cJkWcz-3GLwqDPGig/node_modules/@capacitor/push-notifications"),
        .package(name: "CapacitorShare", path: "../../../node_modules/.store/@capacitor/share@8.0.1-yue3XlxSeXJ0C6c_yjIc8w/node_modules/@capacitor/share"),
        .package(name: "CapgoCapacitorUpdater", path: "../../../node_modules/.store/@capgo/capacitor-updater@8.51.2-ZSB30ym61a7FZK3ULe4FLw/node_modules/@capgo/capacitor-updater")
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
                .product(name: "CapacitorPushNotifications", package: "CapacitorPushNotifications"),
                .product(name: "CapacitorShare", package: "CapacitorShare"),
                .product(name: "CapgoCapacitorUpdater", package: "CapgoCapacitorUpdater")
            ]
        )
    ]
)
