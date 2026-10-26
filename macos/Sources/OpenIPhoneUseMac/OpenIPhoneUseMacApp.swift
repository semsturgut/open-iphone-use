import AppKit
import SwiftUI
import WebKit
import OpenIPhoneUseCore

@main
struct OpenIPhoneUseMacApp: App {
    private var serverURL: URL? {
        guard let value = ProcessInfo.processInfo.environment["OIU_SERVER_URL"],
              let url = URL(string: value),
              url.scheme == "https" || (url.scheme == "http" && ["localhost", "127.0.0.1"].contains(url.host ?? "")) else { return nil }
        return url
    }

    var body: some Scene {
        WindowGroup {
            if let serverURL {
                ServerClientView(url: serverURL).frame(minWidth: 700, minHeight: 660)
            } else if ProcessInfo.processInfo.environment["OIU_SERVER_URL"] != nil {
                Text("OIU_SERVER_URL must be an HTTPS URL or localhost HTTP URL.")
                    .padding().frame(width: 520, height: 200)
            } else {
                StandaloneMacView()
            }
        }
        .windowResizability(.contentSize)
        .windowStyle(.hiddenTitleBar)
    }
}

// The controller is created only for legacy standalone mode. Client mode starts no radio,
// Unix broker or receiver, and uses the same authenticated grid as any LAN browser.
private struct StandaloneMacView: View {
    @StateObject private var controller = OpenIPhoneUseController()
    var body: some View {
        OnboardingView(controller: controller)
            .frame(width: 520, height: 660).background(ClawPalette.ink)
    }
}

private struct ServerClientView: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.load(URLRequest(url: url))
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
}

enum ClawPalette {
    static let ink = Color(red: 0.055, green: 0.067, blue: 0.086)
    static let raised = Color(red: 0.098, green: 0.114, blue: 0.141)
    static let line = Color(red: 0.176, green: 0.200, blue: 0.239)
    static let paper = Color(red: 0.945, green: 0.953, blue: 0.965)
    static let muted = Color(red: 0.545, green: 0.588, blue: 0.647)
    static let signal = Color(red: 0.235, green: 0.878, blue: 0.639)
    static let alert = Color(red: 1.0, green: 0.686, blue: 0.302)
}

private enum OnboardingStep: Int, CaseIterable {
    case connect, pair, share

    var title: String {
        switch self {
        case .connect: "Connect the radio"
        case .pair: "Pair the phone"
        case .share: "Share the screen"
        }
    }
}

struct OnboardingView: View {
    @ObservedObject var controller: OpenIPhoneUseController
    @State private var copied = false

    /// Which step the person is on, derived from what the app actually observes rather than
    /// tracked separately, so the screen can never disagree with the devices.
    private var step: OnboardingStep {
        if controller.isConnected { return .share }
        if controller.isAdvertising { return .pair }
        return .connect
    }

    private var isSharing: Bool {
        controller.isCaptureLive && (controller.latestFrameAgeMilliseconds ?? .max) <= 5_000
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header

            Group {
                if isSharing {
                    MirrorView(controller: controller, onCopy: copyURL)
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .scale(scale: 0.985)),
                            removal: .opacity
                        ))
                } else {
                    SetupSteps(controller: controller, step: step, copied: copied, onCopy: copyURL)
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .offset(y: 8)),
                            removal: .opacity
                        ))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .animation(.smooth(duration: 0.34), value: isSharing)
            .animation(.smooth(duration: 0.28), value: step)

            footer
        }
        .background(ClawPalette.ink)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Wordmark()
            Spacer()
            ConnectionBadge(connected: controller.isConnected,
                            advertising: controller.isAdvertising,
                            sharing: isSharing)
        }
        .padding(.horizontal, 26)
        .padding(.top, 28)
        .padding(.bottom, 22)
    }

    /// The URL is the one thing a person has to move from this window to the phone, so it is the
    /// only control that gets a filled button.
    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !isSharing, let url = controller.receiverAddress, let link = URL(string: url) {
                // Copy is the primary action because the link has to reach the phone; Share is
                // there for sending it to a person, and opens the system picker with AirDrop.
                HStack(spacing: 9) {
                    Button(action: copyURL) {
                        HStack(spacing: 7) {
                            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                            Text(copied ? "Copied" : "Copy link")
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(ClawPalette.ink)
                    .background(ClawPalette.signal, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .accessibilityLabel(copied ? "Copied" : "Copy capture link")

                    ShareLink(item: link, subject: Text("Open iPhone Use capture link"),
                              message: Text("Open this in iPhone Safari, then tap Start sharing.")) {
                        HStack(spacing: 7) {
                            Image(systemName: "square.and.arrow.up")
                            Text("Share")
                        }
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(ClawPalette.paper)
                        .padding(.horizontal, 15)
                        .padding(.vertical, 11)
                        .background(ClawPalette.raised, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .strokeBorder(ClawPalette.line, lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Share capture link")
                }

                Text(displayCaptureAddress)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(ClawPalette.muted)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let path = controller.rootCertificatePath {
                    ShareLink(item: URL(fileURLWithPath: path)) {
                        Label("Share root certificate", systemImage: "checkmark.shield")
                            .font(.system(size: 11.5))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(ClawPalette.muted)
                    Text("First use: install and trust this public certificate on your iPhone before opening the capture link.")
                        .font(.system(size: 10.5))
                        .foregroundStyle(ClawPalette.muted)
                }
            } else if isSharing {
                Text("Screen sharing is live. Close the tab or tap Stop sharing on the phone to end it.")
                    .font(.system(size: 11.5))
                    .foregroundStyle(ClawPalette.muted)
            }

            if let error = controller.lastError {
                Text(error)
                    .font(.system(size: 11))
                    .foregroundStyle(ClawPalette.alert)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 26)
        .padding(.bottom, 24)
    }

    private var displayCaptureAddress: String {
        guard let address = controller.receiverAddress,
              var components = URLComponents(string: address) else { return "" }
        components.query = nil
        return (components.string ?? "") + "?token=••••••••"
    }

    private func copyURL() {
        guard let url = controller.receiverAddress else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url, forType: .string)
        withAnimation(.smooth(duration: 0.2)) { copied = true }
        Task {
            try? await Task.sleep(for: .seconds(2))
            withAnimation(.smooth(duration: 0.2)) { copied = false }
        }
    }
}

// MARK: - steps

private struct SetupSteps: View {
    @ObservedObject var controller: OpenIPhoneUseController
    let step: OnboardingStep
    let copied: Bool
    let onCopy: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            StepRow(index: 1, title: OnboardingStep.connect.title,
                    detail: "Open iPhone Use is a Bluetooth keyboard and pointer for your iPhone. It starts its radio on launch.",
                    state: step == .connect ? .current : .done,
                    action: nil, actionTitle: nil)

            StepRow(index: 2, title: OnboardingStep.pair.title,
                    detail: "On the iPhone: Settings, Accessibility, Touch, AssistiveTouch, Devices, Bluetooth Devices. Choose Open iPhone Use.",
                    state: step == .share ? .done : (step == .pair ? .current : .waiting),
                    action: { controller.isAdvertising ? controller.stopHID() : controller.startHID() },
                    actionTitle: controller.isAdvertising ? "Stop HID" : "Start HID")

            StepRow(index: 3, title: OnboardingStep.share.title,
                    detail: "Open the link below in iPhone Safari, tap Start sharing, and allow the broadcast. The screen appears here as soon as it arrives.",
                    state: step == .share ? .current : .waiting,
                    action: nil, actionTitle: nil)

        }
        .padding(.horizontal, 26)
    }
}

private enum StepState { case done, current, waiting }

private struct StepRow: View {
    let index: Int
    let title: String
    let detail: String
    let state: StepState
    let action: (() -> Void)?
    let actionTitle: String?

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            marker
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.system(size: 14.5, weight: state == .current ? .semibold : .medium))
                    .foregroundStyle(state == .waiting ? ClawPalette.muted : ClawPalette.paper)

                Text(detail)
                    .font(.system(size: 11.5))
                    .foregroundStyle(ClawPalette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .opacity(state == .current || state == .done ? 1 : 0.55)

                if let action, let actionTitle {
                    Button(action: action) {
                        Text(actionTitle)
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(ClawPalette.ink)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 7)
                    .background(ClawPalette.signal, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                    .padding(.top, 3)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.bottom, 20)
    }

    private var marker: some View {
        ZStack {
            Circle()
                .strokeBorder(state == .waiting ? ClawPalette.line : ClawPalette.signal.opacity(0.75),
                              lineWidth: 1.4)
                .frame(width: 20, height: 20)
            if state == .done {
                Image(systemName: "checkmark")
                    .font(.system(size: 9.5, weight: .bold))
                    .foregroundStyle(ClawPalette.signal)
            } else {
                Text("\(index)")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(state == .current ? ClawPalette.paper : ClawPalette.muted)
            }
        }
    }
}

// MARK: - mirror

private struct MirrorView: View {
    @ObservedObject var controller: OpenIPhoneUseController
    let onCopy: () -> Void
    @State private var appeared = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text("iPhone")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(ClawPalette.paper)
                Text(controller.isConnected ? "connected" : "sharing")
                    .font(.system(size: 11))
                    .foregroundStyle(ClawPalette.muted)
                Spacer()
                if let age = controller.latestFrameAgeMilliseconds {
                    Text("\(age) ms")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(ClawPalette.muted.opacity(0.8))
                }
            }
            .padding(.horizontal, 26)

            if let frame = controller.latestFrame,
               let image = NSImage(data: frame.jpeg) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(ClawPalette.line, lineWidth: 1)
                    )
                    .shadow(color: .black.opacity(0.35), radius: 22, y: 10)
                    .padding(.horizontal, 26)
                    .opacity(appeared ? 1 : 0)
                    .scaleEffect(appeared ? 1 : 0.99)
                    .onAppear {
                        withAnimation(.smooth(duration: 0.45)) { appeared = true }
                    }
            }
        }
    }
}

// MARK: - chrome

private struct Wordmark: View {
    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "hand.point.up.left.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(ClawPalette.signal)
            Text("Open iPhone Use")
                .font(.system(size: 15.5, weight: .semibold, design: .rounded))
                .foregroundStyle(ClawPalette.paper)
                .tracking(0.2)
        }
    }
}

private struct ConnectionBadge: View {
    let connected: Bool
    let advertising: Bool
    let sharing: Bool

    private var label: String {
        if connected && sharing { return "Live" }
        if connected { return "Paired" }
        if advertising { return "Waiting" }
        return "Off"
    }

    private var tint: Color {
        if connected && sharing { return ClawPalette.signal }
        if connected || advertising { return ClawPalette.alert }
        return ClawPalette.muted
    }

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(tint)
                .frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(tint)
        }
    }
}
