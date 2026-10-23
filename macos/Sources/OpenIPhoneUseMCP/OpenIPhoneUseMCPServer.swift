import Foundation
import MCP
import OpenIPhoneUseCore

private let tools: [Tool] = [
    Tool(name: "ensure_session", description: "Start here for every Open iPhone Use iPhone task. Return the current screen, check Bluetooth and live capture, and reuse compatible screen calibration. Only calibrates when needed; a compatible session stays in its current app. No setup prompt or device argument needed.", inputSchema: schema(properties: ["device_id": string("Optional; defaults to iphone")], required: [])),
    Tool(name: "list_devices", description: "List the one iPhone HID slot currently supported by this Mac app.", inputSchema: schema(properties: [:], required: []), annotations: .init(readOnlyHint: true)),
    Tool(name: "get_device_status", description: "Get Bluetooth connection and live iPhone capture status. Use ensure_session to start a task.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID, currently iphone")], required: ["device_id"]), annotations: .init(readOnlyHint: true)),
    Tool(name: "get_latest_screenshot", description: "Return the newest in-memory JPEG as image content and its frame ID, capture time, age, and stream status.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID, currently iphone")], required: ["device_id"]), annotations: .init(readOnlyHint: true)),
    Tool(name: "move_pointer", description: "Move the iPhone pointer by relative BLE HID deltas, delivered as a paced ease-in/ease-out glide so iOS acceleration reads it as smooth motion. Requires the current screenshot frame ID; deltas are limited to -127...127.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "expected_frame_id": string("Optional: frame_id from the fresh screenshot; omit only for HID transport diagnostics"), "dx": integer("Horizontal delta, -127...127"), "dy": integer("Vertical delta, -127...127"), "steps": integer("Optional glide step count; defaults to distance/4 clamped 4...48"), "interval_ms": integer("Optional delay between steps, 8...60 ms")], required: ["device_id", "dx", "dy"])),
    Tool(name: "click", description: "Click the iPhone pointer. Omit expected_frame_id only for HID transport diagnostics.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "expected_frame_id": string("frame_id from the fresh screenshot being acted on"), "button": string("left, right, or middle")], required: ["device_id", "button"])),
    Tool(name: "scroll", description: "Scroll a list, grid page or settings screen. Positive dy scrolls down, about 400 is a screenful. Uses the HID wheel, so the pointer does not move. For a full-screen video feed such as Reels, TikTok or Shorts use advance_feed instead: a single wheel event is swallowed while the video plays.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "dy": integer("Wheel delta; 100 is roughly a screenful in most apps, negative scrolls back")], required: ["device_id", "dy"])),
    Tool(name: "advance_feed", description: "Next/previous reel, TikTok or YouTube Short. Sends one centered swipe (dy=1 next, -1 previous). Inspect account/title in the returned frame before another item; video pixel motion is not item-change proof. Optional mode=wheel uses a centered wheel pulse without blind playback taps; steps=120 default, tune explicitly per app. Never claims an exact item count from HID acceptance.", inputSchema: schema(properties: ["device_id": string("Optional device"), "dy": integer("1 next or -1 previous; one transition per call"), "mode": string("swipe (default) or wheel"), "steps": integer("Wheel fallback amount, 1...1200, default 120")], required: ["dy"])),
    Tool(name: "toggle_playback", description: "Play or pause the current video without moving to another one. Use it for \"pause\", \"stop the video\" or \"resume\" in Reels, TikTok or Shorts.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"])),
    Tool(name: "move_assistive_touch", description: "Drag the AssistiveTouch button out of the way when its menu covers the screen. There is no key that closes that menu, so this presses, walks to a corner and releases. Defaults to a long drag toward the top-left.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "dx": integer("Horizontal drag, default -600"), "dy": integer("Vertical drag, default -600")], required: ["device_id"])),
    Tool(name: "type_text", description: "Type up to 500 supported ASCII characters on the iPhone. The whole string is validated before sending. Inspect a fresh screenshot first; expected_frame_id is optional and accepts a known frame from the current capture session up to five seconds old when supplied.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "expected_frame_id": string("Optional: frame_id from the fresh screenshot"), "text": string("Text to type; US keyboard mapping, ASCII only")], required: ["device_id", "text"])),
    Tool(name: "send_key", description: "Send one key or a chord. Plain keys: enter, tab, escape, backspace, delete, space, up, down, left, right, home, end, pageup, pagedown. Chords use +, for example cmd+h (iPhone Home Screen) or cmd+space (system Search). Inspect a fresh screenshot first; expected_frame_id is optional and accepts a known frame from the current capture session up to five seconds old when supplied.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "expected_frame_id": string("Optional: frame_id from the fresh screenshot"), "key": string("Key name, or a + chord such as cmd+h")], required: ["device_id", "key"])),
    Tool(name: "guide", description: "How to drive this iPhone from an agent: the exact workflow, the keyboard shortcuts that work, and the current limits. Read this first.", inputSchema: schema(properties: [:], required: []), annotations: .init(readOnlyHint: true)),
    Tool(name: "prepare_session", description: "Bring the phone to a ready state in one call: wait for the owner's screen share, open the calibration board, measure the pointer, close the board and return to the Home Screen. Run this once before a task.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"])),
    Tool(name: "set_page_board", description: "Show or hide the calibration board on the iPhone capture page. The board is a white grid that reports where a click landed, which is what makes pointer calibration measurable.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "aim": boolean("true opens the board, false closes it")], required: ["device_id", "aim"])),
    Tool(name: "pointer_calibrate", description: "Measure how many BLE HID mouse units cross the iPhone screen. Homes the pointer into the corner, moves a known distance, finds the pointer by diffing two frames and fits the mapping. Run this before pointer_tap.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"])),
    Tool(name: "pointer_quick_probe", description: "Quick startup calibration: two movements and two measured clicks on the visible Safari board using fast16. No profile search or extra hover tour. No click unless a fresh frame shows the pointer inside the board. This is a two-point estimate, not independent full-screen validation. Automatic in ensure_session when needed. Use pointer_probe only for explicit full diagnostics.", inputSchema: schema(properties: ["device_id": string("Optional device")], required: [])),
    Tool(name: "pointer_probe", description: "Full calibration with held-out validation on the visible Safari board. Optional profile burst127, turbo32, fast16, balanced8 or precise4; profile changes require remeasurement.", inputSchema: schema(properties: ["device_id": string("Optional device"), "profile": string("Motion profile; defaults to current")], required: [])),
    Tool(name: "pointer_home", description: "Slam the pointer into the top-left corner so its position is known again. Cancels accumulated drift.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"])),
    Tool(name: "pointer_locate", description: "Find the pointer on screen by nudging it and diffing two frames. Returns its position as screen fractions.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"]), annotations: .init(readOnlyHint: true)),
    Tool(name: "pointer_taps", description: "List the taps the Safari calibration board caught in the last two minutes, as screen fractions. Useful to see exactly where a click landed.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"]), annotations: .init(readOnlyHint: true)),
    Tool(name: "pointer_move", description: "Move the pointer to a screen fraction. x and y are 0...1 with the origin at the top-left. Set aim to find the pointer first and correct the miss before stopping.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "x": number("Horizontal screen fraction, 0...1"), "y": number("Vertical screen fraction, 0...1"), "aim": boolean("Diagnostic only: locate the pointer by frame differences; unreliable on animated screens. Leave false for normal app tasks.")], required: ["device_id", "x", "y"])),
    Tool(name: "pointer_jump_tap", description: "Compatibility alias for pointer_tap; same calibrated burst profile. Inspect the returned frame. No independent faster or differently calibrated path.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "x": number("Horizontal screen fraction, 0...1"), "y": number("Vertical screen fraction, 0...1")], required: ["device_id", "x", "y"])),
    Tool(name: "pointer_jump", description: "Compatibility alias for pointer_move; same calibrated burst profile.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "x": number("Horizontal screen fraction, 0...1"), "y": number("Vertical screen fraction, 0...1")], required: ["device_id", "x", "y"])),
    Tool(name: "pointer_tap", description: "Tap the visible iPhone UI at whole-screen fractions x/y in 0...1, origin top-left. Call ensure_session first. No Safari viewport conversion. Inspect the returned image before continuing.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "x": number("Horizontal screen fraction, 0...1"), "y": number("Vertical screen fraction, 0...1"), "aim": boolean("Diagnostic only: locate the pointer by frame differences; may fail on normal app screens. Leave false.")], required: ["device_id", "x", "y"])),
    Tool(name: "pointer_tap_probed", description: "Tap a screen fraction and read the exact landing from the Safari calibration board, then learn from it. Retries once by nudging the measured miss. Use this while the board is open to build up accuracy; use pointer_tap once the board is closed.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID"), "x": number("Horizontal screen fraction, 0...1"), "y": number("Vertical screen fraction, 0...1"), "steps": integer("Attempts, defaults to 2")], required: ["device_id", "x", "y"])),
    Tool(name: "swipe", description: "Press, drag and release at safe central screen fractions. direction=up/down/left/right. For a reel use advance_feed, then inspect its title/account. No blind pause/resume taps.", inputSchema: schema(properties: ["device_id": string("Optional device"), "direction": string("up, down, left or right")], required: ["direction"])),
    Tool(name: "pointer_retry_tap", description: "One bounded correction of the immediately preceding pointer_tap using its actual observed landing in whole-screen fractions. Requires previous_command_id and fresh post-tap expected_frame_id. Do not guess landing coordinates from an unchanged screen. Rejects drift over 8%, stale/duplicate feedback and chained retries. Does not relearn global calibration from app/video animation. Never retry a submission with uncertain outcome.", inputSchema: schema(properties: ["device_id": string("Optional device"), "previous_command_id": string("command_id of immediately preceding pointer_tap"), "expected_frame_id": string("Fresh frame showing that tap's landing"), "observed_x": number("Observed landing x, 0...1"), "observed_y": number("Observed landing y, 0...1")], required: ["previous_command_id", "expected_frame_id", "observed_x", "observed_y"])),
    Tool(name: "stop_control", description: "Cancel typing and release all held keyboard keys and pointer buttons for this device.", inputSchema: schema(properties: ["device_id": string("Stable Open iPhone Use device ID")], required: ["device_id"]))
]

private func schema(properties: [String: Value], required: [String]) -> Value {
    .object([
        "type": .string("object"),
        "properties": .object(properties),
        "required": .array(required.filter { $0 != "device_id" }.map(Value.string)),
        "additionalProperties": .bool(false),
    ])
}

private func string(_ description: String) -> Value {
    .object(["type": .string("string"), "description": .string(description)])
}

private func integer(_ description: String) -> Value {
    .object(["type": .string("integer"), "description": .string(description)])
}

private func number(_ description: String) -> Value {
    .object(["type": .string("number"), "description": .string(description)])
}

private func boolean(_ description: String) -> Value {
    .object(["type": .string("boolean"), "description": .string(description)])
}

@main
struct OpenIPhoneUseMCPServer {
    static func main() async throws {
        let server = Server(name: "open-iphone-use-mac", version: "0.0.1", instructions: AgentWorkflow.instructions, capabilities: .init(tools: .init()))
        await server.withMethodHandler(ListTools.self) { _ in .init(tools: tools) }
        await server.withMethodHandler(CallTool.self) { params in
            do {
                let request = try makeRequest(params)
                let response = try UnixSocketBrokerClient.request(request)
                var content: [Tool.Content] = [
                    .text(text: response.message + (response.commandID.map { "\ncommand_id=\($0)" } ?? ""), annotations: nil, _meta: nil)
                ]
                if let frame = response.frame {
                    content.append(.image(data: frame.base64, mimeType: frame.mimeType, annotations: nil, _meta: nil))
                    let metadata = "frame_id=\(frame.frameID)\ncaptured_at=\(ISO8601DateFormatter().string(from: frame.capturedAt))\nage_ms=\(frame.ageMilliseconds)\ncapture_active=\(frame.captureActive)"
                    content.append(.text(text: metadata, annotations: nil, _meta: nil))
                }
                let summary = MCPToolOutput(
                    ok: response.ok,
                    message: response.message,
                    commandID: response.commandID,
                    devices: response.devices,
                    status: response.status,
                    frame: response.frame.map {
                        MCPFrameMetadata(frameID: $0.frameID, capturedAt: ISO8601DateFormatter().string(from: $0.capturedAt),
                                         ageMilliseconds: $0.ageMilliseconds, captureActive: $0.captureActive,
                                         mimeType: $0.mimeType)
                    }
                )
                return try .init(content: content, structuredContent: summary, isError: !response.ok)
            } catch {
                return .init(content: [.text(text: error.localizedDescription, annotations: nil, _meta: nil)], isError: true)
            }
        }

        let transport = CompatibleStdioTransport()
        try await server.start(transport: transport)
        while !Task.isCancelled { try await Task.sleep(for: .seconds(60)) }
    }

    private static func makeRequest(_ params: CallTool.Parameters) throws -> BrokerRequest {
        let args = params.arguments ?? [:]
        func requiredString(_ name: String) throws -> String {
            if name == "device_id", args[name] == nil { return "iphone" }
            guard let value = args[name]?.stringValue, !value.isEmpty else {
                throw MCPError.invalidParams("Missing string argument: \(name)")
            }
            return value
        }
        func double(_ name: String) throws -> Double {
            if let value = args[name]?.doubleValue { return value }
            if let value = args[name]?.intValue { return Double(value) }
            throw MCPError.invalidParams("Missing number argument: \(name)")
        }
        func int(_ name: String) throws -> Int {
            guard let value = args[name]?.intValue else { throw MCPError.invalidParams("Missing integer argument: \(name)") }
            return value
        }
        switch params.name {
        case "list_devices", "guide": return BrokerRequest(operation: params.name)
        case "get_device_status", "get_latest_screenshot", "stop_control":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"))
        case "move_pointer":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 expectedFrameID: args["expected_frame_id"]?.stringValue, dx: try int("dx"), dy: try int("dy"),
                                 steps: args["steps"]?.intValue, intervalMilliseconds: args["interval_ms"]?.intValue)
        case "pointer_calibrate", "pointer_home", "pointer_locate", "pointer_taps":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"))
        case "prepare_session", "ensure_session":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"))
        case "set_page_board":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 aim: args["aim"]?.boolValue)
        case "pointer_tap_probed":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 steps: args["steps"]?.intValue, x: try double("x"), y: try double("y"))
        case "pointer_quick_probe":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"))
        case "pointer_probe":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 profile: args["profile"]?.stringValue)
        case "pointer_jump", "pointer_jump_tap":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 x: try double("x"), y: try double("y"))
        case "pointer_move", "pointer_tap":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 x: try double("x"), y: try double("y"), aim: args["aim"]?.boolValue)
        case "scroll":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 dy: try int("dy"))
        case "advance_feed":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 dy: try int("dy"), steps: args["steps"]?.intValue,
                                 profile: args["mode"]?.stringValue)
        case "swipe":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 profile: try requiredString("direction"))
        case "pointer_retry_tap":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 expectedFrameID: try requiredString("expected_frame_id"),
                                 previousCommandID: try requiredString("previous_command_id"),
                                 observedX: try double("observed_x"), observedY: try double("observed_y"))
        case "toggle_playback":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"))
        case "move_assistive_touch":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 dx: args["dx"]?.intValue ?? -600, dy: args["dy"]?.intValue ?? -600)
        case "click":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 expectedFrameID: args["expected_frame_id"]?.stringValue, button: try requiredString("button"))
        case "type_text":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 expectedFrameID: args["expected_frame_id"]?.stringValue, text: try requiredString("text"))
        case "send_key":
            return BrokerRequest(operation: params.name, deviceID: try requiredString("device_id"),
                                 expectedFrameID: args["expected_frame_id"]?.stringValue, key: try requiredString("key"))
        default: throw MCPError.invalidParams("Unknown tool: \(params.name)")
        }
    }
}

private struct MCPToolOutput: Codable {
    let ok: Bool
    let message: String
    let commandID: String?
    let devices: [DeviceSummary]?
    let status: DeviceStatus?
    let frame: MCPFrameMetadata?
}

private struct MCPFrameMetadata: Codable {
    let frameID: String
    let capturedAt: String
    let ageMilliseconds: Int
    let captureActive: Bool
    let mimeType: String
}
