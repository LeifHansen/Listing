// swift-tools-version: 5.9
import PackageDescription

// On-device background removal for the iOS app. Built by `npx cap sync`
// through the app's CapApp-SPM package like every other plugin; the
// deployment floor stays the app's (iOS 15) and the Vision request is
// guarded to iOS 17 at runtime, so an older iPhone simply reports itself
// unsupported and the app falls back.
let package = Package(
    name: "ThryftCutout",
    platforms: [.iOS(.v15)],
    products: [
        .library(
            name: "ThryftCutout",
            targets: ["ThryftCutoutPlugin"])
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", from: "8.0.0")
    ],
    targets: [
        .target(
            name: "ThryftCutoutPlugin",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm"),
                .product(name: "Cordova", package: "capacitor-swift-pm")
            ],
            path: "ios/Sources/ThryftCutoutPlugin")
    ]
)
