import Foundation

private enum OpenIPhoneUseCLI {
    private static let name = "open-iphone-use"
    private static let mcpName = "open-iphone-use"
    private static let dshEntryID = "mcp-open-iphone-use"

    static func main() -> Int32 {
        let args = Array(CommandLine.arguments.dropFirst())
        let command = args.first ?? "help"
        do {
            switch command {
            case "help", "--help", "-h": printHelp()
            case "--version", "version": print("Open iPhone Use 0.0.2")
            case "serve", "devices", "add", "action", "token": try runPortable(args)
            case "mcp":
                if args.contains("--portable") { try runPortable(args.filter { $0 != "--portable" }) }
                else { try runMCPServer() }
            case "install":
                let cliPath = try installCLI()
                print("Installed CLI: \(cliPath.path)")
                if !isOnPATH(cliPath.deletingLastPathComponent().path) {
                    print("Added ~/.local/bin to ~/.zprofile. Open a new Terminal window to use `open-iphone-use`.")
                }
                try installAgentSkill()
                try setupClients(selection: parseSelection(Array(args.dropFirst())))
            case "setup", "configure":
                try installAgentSkill()
                try setupClients(selection: parseSelection(Array(args.dropFirst())))
            case "status":
                if args.contains("--device") { try runPortable(args) } else { try showStatus() }
            default:
                fputs("Unknown command: \(command)\n\n", stderr)
                printHelp()
                return 2
            }
            return 0
        } catch {
            fputs("open-iphone-use: \(error.localizedDescription)\n", stderr)
            return 1
        }
    }

    private static func printHelp() {
        print("""
        Open iPhone Use — local MCP bridge for your paired iPhone

        Usage:
          open-iphone-use install [--opencode] [--deepseek-harness]
          open-iphone-use setup [--opencode] [--deepseek-harness]
          open-iphone-use status
          open-iphone-use mcp
          open-iphone-use serve [--host ADDRESS --cert PEM --key PEM]
          open-iphone-use devices
          open-iphone-use status --device ID
          open-iphone-use action --device ID OPERATION --arguments JSON
          open-iphone-use mcp --portable

        Portable commands require the Python server package in OIU_SERVER_PYTHON's environment.
        Use OIU_STATE and OIU_URL to select the server; see docs/headless-server.md.

        install  Adds this CLI to ~/.local/bin and connects detected agent clients.
        setup    Registers the local MCP server with OpenCode and/or DeepSeek Harness.
        status   Checks the local app and client registrations without showing config secrets.
        mcp      Starts the stdio MCP server (normally launched by an MCP client).

        install/setup also installs a shared agent skill in ~/.agents/skills.
        If no client flags are supplied, setup connects every supported client found on this Mac.
        """)
    }

    private struct Selection {
        var openCode = false
        var deepSeekHarness = false
        var explicit = false
    }

    private static func parseSelection(_ args: [String]) -> Selection {
        var result = Selection()
        for arg in args {
            switch arg {
            case "--opencode": result.openCode = true; result.explicit = true
            case "--deepseek-harness", "--dsh": result.deepSeekHarness = true; result.explicit = true
            case "--all": result.openCode = true; result.deepSeekHarness = true; result.explicit = true
            case "--no-agents": result.explicit = true
            default: break
            }
        }
        return result
    }

    private static func setupClients(selection requested: Selection) throws {
        var selection = requested
        if !selection.explicit {
            selection.openCode = locateOpenCode() != nil
            selection.deepSeekHarness = FileManager.default.fileExists(atPath: dshDesktopProfile.path)
                || FileManager.default.fileExists(atPath: "/Applications/DeepSeek Harness.app")
                || FileManager.default.fileExists(atPath: "\(home.path)/Applications/DeepSeek Harness.app")
        }

        guard selection.openCode || selection.deepSeekHarness else {
            print("No supported agent client was detected. Use `open-iphone-use setup --all` to configure both.")
            return
        }

        if selection.openCode {
            try configureOpenCode()
        }
        if selection.deepSeekHarness {
            try configureDeepSeekHarness()
        }
    }

    private static func showStatus() throws {
        let executable = try appExecutable(named: name)
        print("App CLI: \(executable.path)")
        let link = home.appendingPathComponent(".local/bin/open-iphone-use")
        if FileManager.default.fileExists(atPath: link.path) {
            print("PATH command: installed at \(link.path)")
        } else {
            print("PATH command: not installed; run `open-iphone-use install` from the app bundle")
        }
        if let binary = locateOpenCode() {
            let result = try run(binary, args: ["mcp", "list"])
            print("OpenCode: \(result.status == 0 && result.stdout.contains(mcpName) ? "registered" : "not registered")")
        } else {
            print("OpenCode: not found")
        }
        let skillPath = home.appendingPathComponent(".agents/skills/open-iphone-use/SKILL.md")
        print("Agent skill: \(FileManager.default.fileExists(atPath: skillPath.path) ? "installed" : "not installed")")
        if FileManager.default.fileExists(atPath: dshDesktopProfile.path) {
            let text = (try? String(contentsOf: dshDesktopProfile, encoding: .utf8)) ?? ""
            print("DeepSeek Harness: \(text.contains("serverName: open-iphone-use") ? "registered in desktop profile" : "not registered")")
        } else {
            print("DeepSeek Harness: desktop profile not found")
        }
    }

    private static func installCLI() throws -> URL {
        let executable = try appExecutable(named: name)
        let bin = home.appendingPathComponent(".local/bin", isDirectory: true)
        let link = bin.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)

        if FileManager.default.fileExists(atPath: link.path) || (try? FileManager.default.destinationOfSymbolicLink(atPath: link.path)) != nil {
            let existing = URL(fileURLWithPath: (try? FileManager.default.destinationOfSymbolicLink(atPath: link.path)) ?? "")
                .resolvingSymlinksInPath().path
            if existing == executable.path { return link }
            guard existing.hasSuffix("/Open iPhone Use.app/Contents/Helpers/open-iphone-use") else {
                throw CLIError.pathOccupied(link.path)
            }
            try FileManager.default.removeItem(at: link)
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: executable)
            try ensureZshPath()
            return link
        }
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: executable)
        try ensureZshPath()
        return link
    }

    private static func installAgentSkill() throws {
        let source = try bundledSkillURL()
        let directory = home.appendingPathComponent(".agents/skills/open-iphone-use", isDirectory: true)
        let destination = directory.appendingPathComponent("SKILL.md")
        let content = try Data(contentsOf: source)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: destination.path) {
            let previous = try Data(contentsOf: destination)
            if previous == content {
                print("Agent skill: already installed in ~/.agents/skills/open-iphone-use")
                return
            }
            let firstBackup = destination.appendingPathExtension("open-iphone-use-backup")
            let backup = (try? Data(contentsOf: firstBackup)) == previous
                ? nil
                : (FileManager.default.fileExists(atPath: firstBackup.path)
                    ? destination.appendingPathExtension("open-iphone-use-backup-\(Int(Date().timeIntervalSince1970))")
                    : firstBackup)
            if let backup {
                try FileManager.default.copyItem(at: destination, to: backup)
                print("Agent skill: saved previous file to \(backup.path)")
            }
        }
        try content.write(to: destination, options: .atomic)
        print("Agent skill: installed in ~/.agents/skills/open-iphone-use")
    }

    private static func bundledSkillURL() throws -> URL {
        let executable = try appExecutable(named: name)
        let url = executable
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Resources/skills/open-iphone-use/SKILL.md")
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            throw CLIError.missingBundledExecutable(url.path)
        }
        return url
    }

    private static func ensureZshPath() throws {
        let profile = home.appendingPathComponent(".zprofile")
        let marker = "# >>> Open iPhone Use CLI >>>"
        let block = "\(marker)\nexport PATH=\"$HOME/.local/bin:$PATH\"\n# <<< Open iPhone Use CLI <<<\n"
        let existing = (try? String(contentsOf: profile, encoding: .utf8)) ?? ""
        if existing.contains(marker) || existing.contains("$HOME/.local/bin") { return }
        var updated = existing
        if !updated.isEmpty && !updated.hasSuffix("\n") { updated += "\n" }
        updated += block
        try updated.write(to: profile, atomically: true, encoding: .utf8)
    }

    private static func configureOpenCode() throws {
        guard let binary = locateOpenCode() else {
            print("OpenCode: not found; skipped")
            return
        }
        let current = try run(binary, args: ["mcp", "list"])
        if current.status == 0 && current.stdout.contains(mcpName) {
            print("OpenCode: already registered globally")
            return
        }
        let server = try appExecutable(named: "open-iphone-use-mcp")
        let result = try run(binary, args: ["mcp", "add", mcpName, "--global", "--", server.path])
        guard result.status == 0 else {
            throw CLIError.clientSetup("OpenCode", result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        let verified = try run(binary, args: ["mcp", "list"])
        guard verified.status == 0 && verified.stdout.contains(mcpName) else {
            throw CLIError.clientSetup("OpenCode", "the CLI did not report the registered server")
        }
        print("OpenCode: registered globally as `open-iphone-use`")
    }

    private static func configureDeepSeekHarness() throws {
        let server = try appExecutable(named: "open-iphone-use-mcp")
        let path = dshDesktopProfile
        let directory = path.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let existing = (try? String(contentsOf: path, encoding: .utf8)) ?? ""
        if existing.contains("serverName: open-iphone-use") || existing.contains("id: \(dshEntryID)") {
            print("DeepSeek Harness: already configured in the desktop profile")
            return
        }
        let trimmed = existing.trimmingCharacters(in: .whitespacesAndNewlines)
        let patch = """
        - insert:
            - id: \(dshEntryID)
              name: '@deepseek-ai/dsh-mcp-client'
              config:
                serverName: open-iphone-use
                transport: stdio
                command: \(yamlString(server.path))
                args: []
                toolCallTimeoutMs: 240000
        """
        var updated: String
        if trimmed.isEmpty || trimmed == "[]" {
            updated = patch + "\n"
        } else {
            guard trimmed.hasPrefix("-") || trimmed.hasPrefix("---") || trimmed.hasPrefix("#") else {
                throw CLIError.invalidDSHPatch(path.path)
            }
            updated = existing
            if !updated.hasSuffix("\n") { updated += "\n" }
            updated += patch + "\n"
        }
        if FileManager.default.fileExists(atPath: path.path) {
            let backup = path.appendingPathExtension("open-iphone-use-backup")
            if !FileManager.default.fileExists(atPath: backup.path) {
                try FileManager.default.copyItem(at: path, to: backup)
            }
        }
        try updated.write(to: path, atomically: true, encoding: .utf8)
        print("DeepSeek Harness: added the MCP server to the desktop profile")
        print("Restart DeepSeek Harness if the MCP tools do not appear after its profile reload.")
    }

    private static func yamlString(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "''") + "'"
    }

    private static func runPortable(_ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [ProcessInfo.processInfo.environment["OIU_SERVER_PYTHON"] ?? "python3", "-m", "oiu.cli"] + arguments
        process.standardInput = FileHandle.standardInput
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 { throw CLIError.serverExited(process.terminationStatus) }
    }

    private static func runMCPServer() throws {
        let helper = try appExecutable(named: "open-iphone-use-mcp")
        let process = Process()
        process.executableURL = helper
        process.standardInput = FileHandle.standardInput
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 { throw CLIError.serverExited(process.terminationStatus) }
    }

    private struct CommandResult {
        let status: Int32
        let stdout: String
        let stderr: String
    }

    private static func run(_ executable: URL, args: [String]) throws -> CommandResult {
        let process = Process()
        process.executableURL = executable
        process.arguments = args
        process.currentDirectoryURL = home
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        try process.run()
        let outData = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return CommandResult(
            status: process.terminationStatus,
            stdout: String(decoding: outData, as: UTF8.self),
            stderr: String(decoding: errData, as: UTF8.self)
        )
    }

    private static func locateOpenCode() -> URL? {
        var candidates: [URL] = []
        if let path = ProcessInfo.processInfo.environment["PATH"] {
            candidates += path.split(separator: ":").map { URL(fileURLWithPath: String($0)).appendingPathComponent("opencode") }
        }
        candidates += [
            home.appendingPathComponent(".opencode/bin/opencode"),
            URL(fileURLWithPath: "/opt/homebrew/bin/opencode"),
            URL(fileURLWithPath: "/usr/local/bin/opencode"),
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    private static func appExecutable(named executableName: String) throws -> URL {
        let invoked = CommandLine.arguments.first ?? name
        var candidates: [URL] = []
        if invoked.contains("/") {
            candidates.append(URL(fileURLWithPath: invoked))
        } else if let path = ProcessInfo.processInfo.environment["PATH"] {
            candidates += path.split(separator: ":").map { URL(fileURLWithPath: String($0)).appendingPathComponent(invoked) }
        }
        let resolved = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0.path) })?
            .resolvingSymlinksInPath()
        guard let resolved else { throw CLIError.cannotLocateApp }
        let result = resolved.deletingLastPathComponent().appendingPathComponent(executableName)
        guard FileManager.default.isExecutableFile(atPath: result.path) else {
            throw CLIError.missingBundledExecutable(result.path)
        }
        return result
    }

    private static var home: URL { FileManager.default.homeDirectoryForCurrentUser }
    private static var dshDesktopProfile: URL {
        home.appendingPathComponent(".dsh/profiles/desktop/cordis.patch.yml")
    }

    private static func isOnPATH(_ path: String) -> Bool {
        (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").contains(Substring(path))
    }
}

private enum CLIError: LocalizedError {
    case cannotLocateApp
    case missingBundledExecutable(String)
    case pathOccupied(String)
    case invalidDSHPatch(String)
    case clientSetup(String, String)
    case serverExited(Int32)

    var errorDescription: String? {
        switch self {
        case .cannotLocateApp:
            "Could not resolve this CLI back to its Open iPhone Use.app bundle. Run it from the app or its installed symlink."
        case .missingBundledExecutable(let path): "Bundled helper or agent skill is missing: \(path)"
        case .pathOccupied(let path): "Refusing to replace an existing file at \(path). Move it and retry."
        case .invalidDSHPatch(let path): "The DeepSeek Harness profile patch is not a YAML patch list; left it unchanged: \(path)"
        case .clientSetup(let client, let details): "Could not configure \(client): \(details.trimmingCharacters(in: .whitespacesAndNewlines))"
        case .serverExited(let code): "MCP server exited with status \(code)."
        }
    }
}

exit(OpenIPhoneUseCLI.main())
