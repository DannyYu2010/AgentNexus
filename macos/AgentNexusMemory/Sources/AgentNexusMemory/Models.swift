import Foundation

enum MemoryMode: String, CaseIterable, Identifiable, Codable {
    case off
    case stateOnly = "state-only"
    case full

    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: "关闭"
        case .stateOnly: "仅项目状态"
        case .full: "完整记忆"
        }
    }

    var subtitle: String {
        switch self {
        case .off: "释放 AgentNexus 相关进程，保留所有数据"
        case .stateOnly: "保留接手与工作日志，不加载长期语义记忆"
        case .full: "启用 Mem0、Embedding、Qdrant 与 Qwen3.8"
        }
    }

    var tag: String {
        switch self {
        case .off: "最省内存"
        case .stateOnly: "推荐"
        case .full: "功能完整"
        }
    }

    var symbol: String {
        switch self {
        case .off: "power"
        case .stateOnly: "externaldrive"
        case .full: "brain.head.profile"
        }
    }
}

struct MCPStatus: Codable {
    let running: Int
    let rssMib: Double
    let enabled: Bool?
    let ready: Bool?
    let reasons: [String]?
}

struct ProjectStateStatus: Codable {
    let available: Bool
}

struct Mem0Status: Codable {
    let enabled: Bool
}

struct QdrantStatus: Codable {
    let running: Bool
    let healthy: Bool
    let label: String
}

struct QwenStatus: Codable {
    let running: Bool
    let healthy: Bool
    let ownedByApp: Bool
    /// "on" or "off": what the user asked for, independent of the mode.
    /// Optional so an older controller binary still decodes instead of
    /// blanking the whole status panel.
    let desired: String?
    let model: String
    let models: [String]

    var isSwitchedOn: Bool { desired == "on" }
}

struct ComponentStatus: Codable {
    let mcp: MCPStatus
    let projectState: ProjectStateStatus
    let mem0: Mem0Status
    let qdrant: QdrantStatus
    let qwen: QwenStatus
}

struct ClientStatus: Codable {
    let configured: Bool
}

struct ControllerPayload: Codable {
    let ok: Bool
    let mode: String?
    let components: ComponentStatus?
    let clients: [String: ClientStatus]?
    let drift: [String]?
    let error: String?
    let updatedAt: String?
}

// --- Read-only data browsing (list-projects / list-memories) ---

struct ProjectCounts: Codable, Hashable {
    let stateItems: Int
    let rawEvents: Int
    let workLogs: Int
    let tasks: Int
    let decisions: Int
}

struct ProjectRecord: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let repoPath: String?
    let counts: ProjectCounts?
    /// Present only when Qdrant was healthy at query time.
    let memories: Int?
}

struct ProjectsPayload: Codable {
    let ok: Bool
    let qdrantHealthy: Bool?
    let projects: [ProjectRecord]?
    /// project_id -> count for memories whose project is not registered.
    let orphanMemories: [String: Int]?
    let error: String?
    let updatedAt: String?
}

struct MemoryRecord: Codable, Identifiable, Hashable {
    let memoryId: String
    let text: String
    let createdAt: String?
    /// Set only in the "all projects" listing.
    let projectId: String?

    var id: String { memoryId }
}

struct MemoriesPayload: Codable {
    let ok: Bool
    let projectId: String?
    let qdrantHealthy: Bool?
    let memories: [MemoryRecord]?
    let error: String?
    let updatedAt: String?
}

struct ProcessResult: Sendable {
    let status: Int32
    let stdout: Data
    let stderr: Data
}
