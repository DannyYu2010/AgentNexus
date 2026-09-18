import AppKit
import Foundation

@MainActor
final class MemoryController: ObservableObject {
    @Published var mode: MemoryMode = .stateOnly
    @Published var payload: ControllerPayload?
    @Published var isBusy = false
    @Published var message = "正在检查状态…"
    @Published var errorMessage: String?

    // Read-only data browsing state.
    @Published var projects: [ProjectRecord] = []
    @Published var memoriesByProject: [String: [MemoryRecord]] = [:]
    @Published var expandedProjectId: String?
    @Published var qdrantHealthy = false
    @Published var orphanMemories: [String: Int] = [:]

    private var controlURL: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("agent-memory-control")
    }

    init() {
        Task { await reconcileOnLaunch() }
    }

    func refresh() async {
        await execute(arguments: ["status"], applying: nil)
    }

    /// Start what the recorded mode says is missing. Opening this window used
    /// to start nothing, so a machine whose components were down still showed
    /// the recorded mode while nothing ran.
    ///
    /// Run unconditionally rather than only when something looks missing: the
    /// controller writes a snapshot of what it found to reconcile.log, and a
    /// post-reboot failure is only visible in the very run that repairs it.
    /// Reconcile costs nothing when everything already answers -- it starts a
    /// component only when that component is down.
    func reconcileOnLaunch() async {
        await execute(arguments: ["reconcile"], applying: nil)
    }

    /// Fill in components the recorded intent wants but that are not answering.
    func reconcile() async {
        guard !isBusy else { return }
        await execute(arguments: ["reconcile"], applying: nil)
    }

    /// True when the recorded intent asks for something that is not running.
    var needsReconcile: Bool {
        guard let components = payload?.components else { return false }
        if mode == .full, !components.qdrant.healthy { return true }
        if components.qwen.isSwitchedOn, !components.qwen.healthy { return true }
        return false
    }

    func apply(_ newMode: MemoryMode) async {
        guard !isBusy else { return }
        await execute(arguments: ["set-mode", newMode.rawValue], applying: newMode)
    }

    /// Start or stop Qwen on its own. The mode is left alone; only the model
    /// server on port 8080 is touched.
    func setQwen(on desired: Bool) async {
        guard !isBusy else { return }
        await execute(arguments: ["set-qwen", desired ? "on" : "off"], applying: nil)
    }

    func openLogs() {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/AgentNexus", isDirectory: true)
        NSWorkspace.shared.open(url)
    }

    // MARK: - Read-only data browsing

    /// Load the registered projects with their record counts. Pure read; the
    /// failure path keeps whatever was on screen instead of clearing it.
    func loadProjects() async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        message = "正在加载项目列表…"
        defer { isBusy = false }
        guard let controlURL else {
            errorMessage = "应用资源中缺少控制器，请重新安装。"
            return
        }
        let result = await Task.detached(priority: .userInitiated) {
            Self.run(controlURL: controlURL, arguments: ["--json", "list-projects"])
        }.value
        do {
            let decoded = try Self.decode(ProjectsPayload.self, from: result.stdout)
            projects = decoded.projects ?? []
            qdrantHealthy = decoded.qdrantHealthy ?? false
            orphanMemories = decoded.orphanMemories ?? [:]
            let liveIDs = Set(projects.map(\.id))
            memoriesByProject = memoriesByProject.filter { liveIDs.contains($0.key) }
            if let current = expandedProjectId, !liveIDs.contains(current) {
                expandedProjectId = nil
            }
            let explanation = [decoded.error, stderrText(result.stderr)]
                .compactMap { $0 }
                .first { !$0.isEmpty }
            if result.status != 0, !decoded.ok {
                errorMessage = explanation ?? "无法读取项目列表"
                message = "操作未完成"
            } else {
                message = orphanMemories.isEmpty
                    ? "已加载 \(projects.count) 个项目"
                    : "已加载 \(projects.count) 个项目 · \(orphanMemories.count) 个未登记记忆来源"
            }
        } catch {
            errorMessage = "无法读取项目列表：\(error.localizedDescription)"
            message = "状态不可用"
        }
    }

    /// Load semantic memories for one project on first expansion; cached after
    /// that. Never overwrites an already-cached list with an empty degraded
    /// answer, so an offline Qdrant does not blank what we already have.
    func loadMemories(for projectId: String) async {
        if memoriesByProject[projectId] != nil {
            expandedProjectId = projectId
            return
        }
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        message = "正在加载「\(projectId)」的记忆…"
        defer { isBusy = false }
        guard let controlURL else {
            errorMessage = "应用资源中缺少控制器，请重新安装。"
            return
        }
        let result = await Task.detached(priority: .userInitiated) {
            Self.run(
                controlURL: controlURL,
                arguments: ["--json", "list-memories", projectId]
            )
        }.value
        do {
            let decoded = try Self.decode(MemoriesPayload.self, from: result.stdout)
            if result.status == 0, decoded.ok {
                memoriesByProject[projectId] = decoded.memories ?? []
                qdrantHealthy = decoded.qdrantHealthy ?? false
                expandedProjectId = projectId
                message = "「\(projectId)」共 \(decoded.memories?.count ?? 0) 条记忆"
            } else {
                errorMessage = decoded.error ?? "无法读取记忆列表"
                message = "操作未完成"
            }
        } catch {
            errorMessage = "无法读取记忆列表：\(error.localizedDescription)"
            message = "状态不可用"
        }
    }

    func toggleProject(_ projectId: String) {
        if expandedProjectId == projectId {
            expandedProjectId = nil
        } else {
            Task { await loadMemories(for: projectId) }
        }
    }

    /// Copy an ID (or any short text) to the pasteboard.
    nonisolated func copyToPasteboard(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    private func stderrText(_ data: Data) -> String? {
        let text = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return text?.isEmpty == false ? text : nil
    }

    private func execute(arguments: [String], applying newMode: MemoryMode?) async {
        guard let controlURL else {
            errorMessage = "应用资源中缺少控制器，请重新安装。"
            return
        }
        isBusy = true
        errorMessage = nil
        message = statusMessage(for: arguments, newMode: newMode)

        let result = await Task.detached(priority: .userInitiated) {
            Self.run(controlURL: controlURL, arguments: arguments)
        }.value

        defer { isBusy = false }
        do {
            let decoded = try Self.decode(result.stdout)
            payload = decoded
            if let rawMode = decoded.mode, let reportedMode = MemoryMode(rawValue: rawMode) {
                mode = reportedMode
            }
            if result.status == 0, decoded.ok {
                message = "状态正常"
            } else {
                let stderr = String(data: result.stderr, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                // Drift alone is not a failed operation: it belongs in the
                // orange line, not in a modal. Only raise an alert when the
                // controller actually has something to say, and never raise an
                // empty one — an empty stderr used to win over a nil error and
                // produce a blank dialog that hid the real state.
                let explanation = [decoded.error, stderr]
                    .compactMap { $0 }
                    .first { !$0.isEmpty }
                if let explanation {
                    errorMessage = explanation
                    message = "操作未完成"
                } else {
                    message = "已应用，但检测到状态偏差"
                }
            }
        } catch {
            let output = String(data: result.stdout, encoding: .utf8) ?? ""
            let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
            errorMessage = "无法读取控制器状态：\(error.localizedDescription)\n\(output)\(stderr)"
            message = "状态不可用"
        }
    }

    private func statusMessage(for arguments: [String], newMode: MemoryMode?) -> String {
        if let newMode { return "正在切换到「\(newMode.title)」…" }
        if arguments.first == "set-qwen" {
            return arguments.last == "on" ? "正在启动 Qwen3.8…" : "正在停止 Qwen3.8…"
        }
        if arguments.first == "reconcile" {
            return "正在按记录的模式补启组件…"
        }
        return "正在检查状态…"
    }

    nonisolated private static func run(controlURL: URL, arguments: [String]) -> ProcessResult {
        // App launches under launchd whose PATH is /usr/bin:/bin:/usr/sbin:/sbin —
        // it does NOT inherit the user's login-shell PATH, so `#!/usr/bin/env
        // python3` cannot find the WorkBuddy-managed interpreter. Probe a
        // short list of well-known paths instead and exec the script directly
        // with the chosen python3; the script's shebang is then bypassed and
        // becomes informational only. Set AGENT_NEXUS_PYTHON3 in the
        // environment to override the probe order.
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let workBuddyPython = "\(home)/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
        let candidates: [String] = [
            ProcessInfo.processInfo.environment["AGENT_NEXUS_PYTHON3"],
            workBuddyPython,
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ].compactMap { $0 }

        let pythonPath = candidates.first {
            FileManager.default.isExecutableFile(atPath: $0)
        } ?? workBuddyPython

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = [controlURL.path, "--json"] + arguments
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return ProcessResult(
                status: 127,
                stdout: Data(),
                stderr: Data(error.localizedDescription.utf8)
            )
        }
        return ProcessResult(
            status: process.terminationStatus,
            stdout: stdout.fileHandleForReading.readDataToEndOfFile(),
            stderr: stderr.fileHandleForReading.readDataToEndOfFile()
        )
    }

    nonisolated private static func decode(_ data: Data) throws -> ControllerPayload {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ControllerPayload.self, from: data)
    }

    nonisolated private static func decode<T: Decodable>(
        _ type: T.Type, from data: Data
    ) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }
}
