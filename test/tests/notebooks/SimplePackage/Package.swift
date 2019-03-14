// swift-tools-version:4.2

import PackageDescription

let package = Package(
    name: "SimplePackage",
    products: [
        .library(name: "SimplePackage", targets: ["SimplePackage"]),
    ],
    dependencies: [],
    targets: [
        .target(
            name: "SimplePackage",
            dependencies: []),
    ]
)
