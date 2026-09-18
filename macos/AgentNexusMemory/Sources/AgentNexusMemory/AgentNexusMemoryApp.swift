import AppKit
import SwiftUI

@main
struct AgentNexusMemoryApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var controller = MemoryController()

    var body: some Scene {
        WindowGroup("AgentNexus Memory") {
            ContentView()
                .environmentObject(controller)
        }
        .windowResizability(.contentSize)
        .defaultSize(width: 1040, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        MenuBarExtra("AgentNexus Memory", systemImage: menuBarSymbol) {
            Text("当前模式：\(controller.mode.title)")
            Divider()
            ForEach(MemoryMode.allCases) { mode in
                Button {
                    Task { await controller.apply(mode) }
                } label: {
                    if controller.mode == mode {
                        Label(mode.title, systemImage: "checkmark")
                    } else {
                        Text(mode.title)
                    }
                }
                .disabled(controller.isBusy)
            }
            Divider()
            Button("刷新状态") { Task { await controller.refresh() } }
            Button("显示主窗口") { NSApplication.shared.activate(ignoringOtherApps: true) }
            Divider()
            Button("退出") { NSApplication.shared.terminate(nil) }
        }
    }

    private var menuBarSymbol: String {
        switch controller.mode {
        case .off: "brain.head.profile.slash"
        case .stateOnly: "externaldrive"
        case .full: "brain.head.profile"
        }
    }
}

/// Disables per-window frame autosave and forces a fixed initial size on
/// launch. Without this, macOS restores whatever height an earlier build
/// accidentally stretched the window to (ContentView frame `maxHeight:
/// .infinity` once pinned it near the screen's max), and SwiftUI's
/// `.defaultSize` only applies when no saved frame exists — so users were
/// stuck with a window that no longer matched the actual content.
final class AppDelegate: NSObject, NSApplicationDelegate {
    static let targetContentSize = NSSize(width: 1040, height: 760)

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let window = NSApplication.shared.windows.first else { return }
        // Empty autosave name disables frame persistence entirely. macOS will
        // no longer write or read the NSWindow Frame defaults key for this
        // window, so a future build cannot re-stretch it either.
        window.setFrameAutosaveName("")
        window.setContentSize(Self.targetContentSize)
        window.center()
    }
}
