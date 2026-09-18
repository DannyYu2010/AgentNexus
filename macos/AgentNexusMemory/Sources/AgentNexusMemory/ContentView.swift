import Foundation
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var controller: MemoryController
    @State private var pendingOffConfirmation = false

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            VStack(alignment: .leading, spacing: 20) {
                header
                VStack(spacing: 10) {
                    ForEach(MemoryMode.allCases) { mode in
                        ModeRow(
                            mode: mode,
                            selected: controller.mode == mode,
                            disabled: controller.isBusy
                        ) {
                            if mode == .off {
                                pendingOffConfirmation = true
                            } else {
                                Task { await controller.apply(mode) }
                            }
                        }
                    }
                }
                qwenSwitch
                componentGrid
                Divider()
                footer
            }
            .padding(26)
            .frame(width: 620, alignment: .top)

            Divider()

            dataSidebar
        }
        .frame(minWidth: 1000)
        .confirmationDialog(
            "关闭 Shared Memory？",
            isPresented: $pendingOffConfirmation,
            titleVisibility: .visible
        ) {
            Button("关闭并释放相关进程", role: .destructive) {
                Task { await controller.apply(.off) }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("项目数据不会被删除。Qdrant 与 Qwen3.8 会一并停止，包括你手工启动的实例；端口上无法识别的进程不会被强制结束。")
        }
        .alert(
            "操作未完成",
            isPresented: Binding(
                get: { controller.errorMessage != nil },
                set: { if !$0 { controller.errorMessage = nil } }
            )
        ) {
            Button("好") { controller.errorMessage = nil }
        } message: {
            Text(controller.errorMessage ?? "未知错误")
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApplication.shared.applicationIconImage)
                .resizable()
                .frame(width: 52, height: 52)
            VStack(alignment: .leading, spacing: 3) {
                Text("AgentNexus Memory")
                    .font(.title2.weight(.semibold))
                Text("当前模式：\(controller.mode.title)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if controller.isBusy {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    /// Qwen has its own switch because the model server is the expensive part:
    /// keeping it resident while the rest is idle, or dropping it without
    /// leaving full memory, are both things worth doing on their own.
    private var qwenSwitch: some View {
        let qwen = controller.payload?.components?.qwen
        return HStack(spacing: 12) {
            Image(systemName: "cpu")
                .frame(width: 22)
                .foregroundStyle(qwen?.isSwitchedOn == true ? Color.accentColor : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text("Qwen3.8 模型服务").font(.headline)
                Text(qwenSwitchSubtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Toggle(
                "",
                isOn: Binding(
                    get: { qwen?.isSwitchedOn ?? false },
                    set: { desired in Task { await controller.setQwen(on: desired) } }
                )
            )
            .labelsHidden()
            .toggleStyle(.switch)
            .disabled(controller.isBusy)
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
        )
    }

    private var qwenSwitchSubtitle: String {
        guard let qwen = controller.payload?.components?.qwen else { return "未检测" }
        if qwen.isSwitchedOn && !qwen.running { return "已开启，但没有响应" }
        if !qwen.isSwitchedOn && qwen.running { return "已关闭，但仍在运行" }
        if qwen.running {
            return qwen.ownedByApp ? "运行中 · 由应用启动" : "运行中 · 外部启动"
        }
        return controller.mode == .full ? "已停止 · 完整记忆需要它" : "已停止 · 不占内存"
    }

    private var componentGrid: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 10) {
            ComponentCell(
                title: "MCP",
                active: mcpReady,
                detail: mcpDetail
            )
            ComponentCell(
                title: "Project State",
                active: controller.payload?.components?.projectState.available ?? false,
                detail: controller.mode == .off ? "不可用" : "可用"
            )
            ComponentCell(
                title: "Mem0",
                active: controller.payload?.components?.mem0.enabled ?? false,
                detail: controller.mode == .full ? "已启用" : "未加载"
            )
            ComponentCell(
                title: "Qdrant",
                active: controller.payload?.components?.qdrant.healthy ?? false,
                detail: controller.payload?.components?.qdrant.healthy == true ? "健康" : "已停止"
            )
            ComponentCell(
                title: "Qwen3.8",
                active: controller.payload?.components?.qwen.healthy ?? false,
                detail: qwenDetail
            )
            // Deliberately "配置", not "接入": this only reads the four config
            // files, so it says nothing about whether a client is running.
            // Whether anything actually connected is the MCP cell's job.
            ComponentCell(
                title: "客户端配置",
                active: clientsConfigured,
                detail: clientsDetail
            )
        }
    }

    /// Read-only browse over every registered project and its semantic
    /// memories, presented as a right-hand sidebar. Loads on demand, caches
    /// per project, copies IDs to the pasteboard. It never mutates data.
    private var dataSidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "list.bullet.rectangle.portrait")
                    .foregroundStyle(.secondary)
                Text("数据浏览").font(.headline)
                if !controller.orphanMemories.isEmpty {
                    Text("\(controller.orphanMemories.count) 个未登记来源")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                Spacer()
                Button(controller.projects.isEmpty ? "加载" : "刷新") {
                    Task { await controller.loadProjects() }
                }
                .disabled(controller.isBusy)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)

            Divider()

            if controller.projects.isEmpty {
                VStack(spacing: 6) {
                    if controller.isBusy {
                        ProgressView()
                            .controlSize(.small)
                        Text("正在加载项目…").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Image(systemName: "folder.badge.questionmark")
                            .font(.title2)
                            .foregroundStyle(.tertiary)
                        Text("尚未加载项目")
                            .font(.callout.weight(.medium))
                        Text("点击上方「加载」查看全部项目\n与语义记忆 ID（只读）")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(controller.projects) { project in
                            DataProjectRow(
                                project: project,
                                isExpanded: controller.expandedProjectId == project.id,
                                memories: controller.memoriesByProject[project.id] ?? [],
                                isLoading: controller.isBusy,
                                onToggle: { controller.toggleProject(project.id) },
                                onCopy: { controller.copyToPasteboard(project.id) },
                                onCopyMemory: { controller.copyToPasteboard($0) }
                            )
                        }
                    }
                    .padding(8)
                }
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
        )
        .padding(14)
        .frame(width: 390)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label(controller.message, systemImage: statusSymbol)
                    .font(.caption)
                    .foregroundStyle(controller.payload?.ok == false ? .orange : .secondary)
                Spacer()
                Button("刷新") { Task { await controller.refresh() } }
                    .disabled(controller.isBusy)
                Button("补启缺失组件") { Task { await controller.reconcile() } }
                    .disabled(controller.isBusy || !controller.needsReconcile)
                Button("查看日志…") { controller.openLogs() }
            }
            if let drift = controller.payload?.drift, !drift.isEmpty {
                Text(drift.joined(separator: "\n"))
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            Text("模式切换不会删除 Project State、长期记忆或备份。关闭时会一并停止 Qdrant 与 Qwen（含手工启动的实例）。")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var mcpDetail: String {
        guard let status = controller.payload?.components?.mcp else { return "未检测" }
        if controller.mode == .off { return "已关闭" }
        if status.ready == false { return "启动器不可用" }
        if status.running == 0 { return "已就绪 · 等待客户端" }
        return "\(status.running) 个 · \(String(format: "%.0f", status.rssMib)) MB"
    }

    private var mcpReady: Bool {
        guard let status = controller.payload?.components?.mcp else { return false }
        return status.ready ?? (status.running > 0)
    }

    private var qwenDetail: String {
        guard let status = controller.payload?.components?.qwen else { return "未检测" }
        if !status.healthy { return status.isSwitchedOn ? "开启中…" : "未运行" }
        return status.ownedByApp ? "应用管理" : "外部运行"
    }

    private var clientsDetail: String {
        guard let clients = controller.payload?.clients, !clients.isEmpty else {
            return "未检测"
        }
        let configured = clients.values.filter(\.configured).count
        return "\(configured)/\(clients.count) 已指向启动器"
    }

    private var clientsConfigured: Bool {
        guard let clients = controller.payload?.clients, !clients.isEmpty else { return false }
        return clients.values.allSatisfy(\.configured)
    }

    private var statusSymbol: String {
        if controller.isBusy { return "arrow.triangle.2.circlepath" }
        if controller.payload?.ok == false { return "exclamationmark.triangle" }
        return "checkmark.circle"
    }
}

private struct ModeRow: View {
    let mode: MemoryMode
    let selected: Bool
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.title3)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                Image(systemName: mode.symbol)
                    .frame(width: 22)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(mode.title).font(.headline)
                    Text(mode.subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(mode.tag)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(selected ? Color.accentColor.opacity(0.09) : Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(selected ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: selected ? 1.5 : 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }
}

private struct ComponentCell: View {
    let title: String
    let active: Bool
    let detail: String

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(active ? Color.green : Color.secondary.opacity(0.45))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption.weight(.medium))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// One registered project. Header toggles expansion; expanded rows list that
/// project's semantic memories. Copy buttons put the relevant ID on the
/// pasteboard. Nothing here writes to the store.
private struct DataProjectRow: View {
    let project: ProjectRecord
    let isExpanded: Bool
    let memories: [MemoryRecord]
    let isLoading: Bool
    let onToggle: () -> Void
    let onCopy: () -> Void
    let onCopyMemory: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .frame(width: 10)
                VStack(alignment: .leading, spacing: 1) {
                    Text(project.id)
                        .font(.system(.callout, design: .monospaced))
                        .fontWeight(.medium)
                        .textSelection(.enabled)
                    if let repoPath = project.repoPath, !repoPath.isEmpty {
                        Text(repoPath)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
                Spacer()
                countsText
                if let memoryCount = project.memories {
                    Text("\(memoryCount) 记忆")
                        .font(.caption2)
                        .foregroundStyle(memoryCount > 0 ? Color.accentColor : .secondary)
                }
                Button(action: onCopy) {
                    Image(systemName: "doc.on.doc")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("复制项目 ID")
            }
            .contentShape(Rectangle())
            .onTapGesture(perform: onToggle)

            if isExpanded {
                memoryRows
                    .padding(.leading, 18)
            }
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 6)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(isExpanded ? Color.accentColor.opacity(0.05) : Color.clear)
        )
    }

    private var countsText: Text {
        guard let counts = project.counts else { return Text("") }
        return Text("state \(counts.stateItems) · raw \(counts.rawEvents) · logs \(counts.workLogs)")
            .font(.caption2)
            .foregroundStyle(.secondary)
    }

    @ViewBuilder
    private var memoryRows: some View {
        if memories.isEmpty {
            Text(isLoading ? "正在加载记忆…" : "无语义记忆（Mem0 尚未抽取）")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.vertical, 4)
        } else {
            ForEach(memories) { memory in
                MemoryRow(memory: memory, onCopy: { onCopyMemory(memory.memoryId) })
            }
        }
    }
}

private struct MemoryRow: View {
    let memory: MemoryRecord
    let onCopy: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(memory.text.isEmpty ? "(空记忆)" : memory.text)
                    .font(.caption)
                    .lineLimit(3)
                    .textSelection(.enabled)
                Text("\(memory.memoryId) · \(shortDate)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 4)
            Button(action: onCopy) {
                Image(systemName: "doc.on.doc")
                    .font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("复制 Memory ID")
        }
        .padding(.vertical, 2)
    }

    private var shortDate: String {
        guard let createdAt = memory.createdAt, createdAt.count >= 10 else {
            return "未知时间"
        }
        return String(createdAt.prefix(10))
    }
}
