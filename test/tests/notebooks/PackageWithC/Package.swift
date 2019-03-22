// swift-tools-version:4.2

import PackageDescription

let package = Package(
    name: "PackageWithC",
    products: [
        .library(name: "PackageWithC", targets: ["PackageWithC1", "PackageWithC2"]),
    ],
    dependencies: [],
    targets: [
        .target(
            name: "PackageWithC1",
            dependencies: []),
        .target(
            name: "PackageWithC2",
            dependencies: []),
    ]
)
