// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "AgentNexusMemory",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AgentNexusMemory", targets: ["AgentNexusMemory"]),
    ],
    targets: [
        .executableTarget(
            name: "AgentNexusMemory",
            path: "Sources/AgentNexusMemory"
        ),
    ]
)
