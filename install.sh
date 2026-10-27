#!/usr/bin/env bash
# Bootstrap a managed installation without a checkout.
set -eu

usage() {
    cat <<'USAGE'
Usage: bash install.sh [--version <tag|branch>] [--no-agents] [--uninstall] [--dry-run] [--verify-downloads]
USAGE
}

main() {
ref= no_agents=0 uninstall=0 dry_run=0 verify_only=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version) [ "$#" -ge 2 ] && [ -n "$2" ] || { echo '--version requires a ref' >&2; exit 2; }; ref=$2; shift ;;
        --no-agents) no_agents=1 ;;
        --uninstall) uninstall=1 ;;
        --dry-run) dry_run=1 ;;
        --verify-downloads) verify_only=1 ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[ "$dry_run" = 0 ] || [ "$verify_only" = 0 ] || { echo 'Choose either --dry-run or --verify-downloads' >&2; exit 2; }
repo=${OIU_REPO:-semsturgut/open-iphone-use}
install_root=${OIU_INSTALL_ROOT:-"$HOME/.local/share/open-iphone-use/runtime"}
bin_dir=${OIU_BIN_DIR:-"$HOME/.local/bin"}
case "$install_root" in /*) ;; *) echo 'OIU_INSTALL_ROOT must be absolute' >&2; exit 2;; esac
case "$bin_dir" in /*) ;; *) echo 'OIU_BIN_DIR must be absolute' >&2; exit 2;; esac
os=${OIU_UNAME:-$(uname -s)}
case "$os" in
    Darwin) platform=macos ;;
    Linux) platform=linux; echo 'Linux support is untested.'; if grep -qi microsoft /proc/version 2>/dev/null; then echo 'WSL generally does not provide Bluetooth access.'; fi ;;
    MINGW*|MSYS*|CYGWIN*) echo 'Native Windows: run in PowerShell: irm https://raw.githubusercontent.com/semsturgut/open-iphone-use/main/setup.ps1 | iex' >&2; exit 1 ;;
    *) echo "Unsupported OS: $os" >&2; exit 1 ;;
esac
if [ "$uninstall" = 1 ]; then
    if [ "$dry_run" = 1 ]; then printf 'Plan: uninstall using %s/setup.sh\n' "$install_root"; exit 0; fi
    if [ -f "$install_root/setup.sh" ] && [ -f "$install_root/.open-iphone-use-managed" ]; then
        exec sh "$install_root/setup.sh" --uninstall
    fi
    if [ ! -f "$install_root/.open-iphone-use-managed" ]; then echo 'No managed installation found; nothing removed.'; exit 0; fi
    echo "Installed setup.sh not found in $install_root; use the setup.sh from your original checkout with --uninstall." >&2; exit 1
fi
for cmd in curl tar; do command -v "$cmd" >/dev/null 2>&1 || { echo "Install $cmd, then re-run install.sh." >&2; exit 1; }; done
if command -v shasum >/dev/null 2>&1; then hash_cmd=shasum
elif command -v sha256sum >/dev/null 2>&1; then hash_cmd=sha256sum
else echo 'Install shasum or sha256sum, then re-run install.sh.' >&2; exit 1; fi
python=${OIU_PYTHON:-python3}
if ! command -v "$python" >/dev/null 2>&1 || ! "$python" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
    echo 'Python 3.11+ not found; setup will use its uv fallback to install Python 3.12.'
fi
if [ -z "$ref" ]; then
    latest=$(curl -sSL -o /dev/null -w '%{http_code} %{url_effective}' "https://github.com/$repo/releases/latest") || { echo 'Could not resolve the latest release; specify --version or check your connection.' >&2; exit 1; }
    case "$latest" in
        "200 https://github.com/$repo/releases/tag/"*) ref=${latest##*/} ;;
        "404 "*|"200 https://github.com/$repo/releases"|"200 https://github.com/$repo/releases/") ref=main; echo 'No published release found; using main.' ;;
        *) echo 'Could not resolve the latest published release; specify --version.' >&2; exit 1 ;;
    esac
fi
case "$ref" in *[!a-zA-Z0-9._/-]*|''|/*|*..*) echo "Invalid ref: $ref" >&2; exit 2;; esac
release_url="https://github.com/$repo/releases/download/$ref/open-iphone-use-$ref.tar.gz"
source_url=${OIU_ARCHIVE_URL:-$release_url}
fallback_url="https://codeload.github.com/$repo/tar.gz/$ref"
helper_url=${OIU_HELPER_URL:-"https://github.com/$repo/releases/download/$ref/open-iphone-use-headless-macos-universal.tar.gz"}
printf 'Ref: %s\nSource: %s\n' "$ref" "$source_url"
[ -n "${OIU_ARCHIVE_URL:-}" ] || printf 'Source fallback: %s\n' "$fallback_url"
printf 'Install root: %s\nCLI directory: %s\n' "$install_root" "$bin_dir"
if [ "$platform" = macos ]; then printf 'Prebuilt helper: %s (used if checksum and architecture checks pass; otherwise build with Swift)\nChecksum: %s.sha256\n' "$helper_url" "$helper_url"; else echo 'Prebuilt helper: not used on Linux'; fi
[ "$dry_run" = 0 ] || exit 0
work=$(mktemp -d "${TMPDIR:-/tmp}/oiu-install.XXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
if ! curl -fsSL "$source_url" -o "$work/source.tar.gz"; then
    if [ -n "${OIU_ARCHIVE_URL:-}" ]; then echo 'Source download failed.' >&2; exit 1; fi
    echo 'Release source unavailable; trying codeload.'
    curl -fsSL "$fallback_url" -o "$work/source.tar.gz" || { echo 'Source download failed.' >&2; exit 1; }
fi
mkdir "$work/source"
tar -xzf "$work/source.tar.gz" -C "$work/source" --strip-components=1
[ -f "$work/source/server/pyproject.toml" ] && [ -f "$work/source/setup.sh" ] || { echo 'Invalid source archive.' >&2; exit 1; }
helper=
if [ "$platform" = macos ]; then
    if curl -fsSL "$helper_url" -o "$work/helper.tar.gz" && curl -fsSL "$helper_url.sha256" -o "$work/helper.sha256"; then
        expected=$(awk 'NR==1 {print $1}' "$work/helper.sha256")
        case "$hash_cmd" in shasum) actual=$(shasum -a 256 "$work/helper.tar.gz" | awk '{print $1}');; *) actual=$(sha256sum "$work/helper.tar.gz" | awk '{print $1}');; esac
        if [ "${#expected}" = 64 ] && [ "$expected" = "$actual" ]; then
            mkdir "$work/helper"
            if tar -xzf "$work/helper.tar.gz" -C "$work/helper" && [ -f "$work/helper/open-iphone-use-headless" ] && [ -x "$work/helper/open-iphone-use-headless" ]; then
                candidate="$work/helper/open-iphone-use-headless"
                xattr -d com.apple.quarantine "$candidate" 2>/dev/null || true
                arch=$(uname -m)
                archs=$(lipo -archs "$candidate" 2>/dev/null || true)
                case " $archs " in *" $arch "*) helper=$candidate;; esac
                # The lipo shim may require Xcode tools; macOS also ships file.
                if [ -z "$helper" ] && [ -z "$archs" ] && command -v file >/dev/null 2>&1; then
                    description=$(file -b "$candidate" 2>/dev/null || true)
                    case "$arch:$description" in
                        arm64:*Mach-O*|x86_64:*Mach-O*)
                            if printf '%s\n' "$description" | grep -Eq "(^|[^[:alnum:]_])$arch([^[:alnum:]_]|$)"; then helper=$candidate; fi ;;
                    esac
                fi
                [ -n "$helper" ] || echo "Prebuilt helper does not contain host architecture $arch." >&2
            else echo 'Invalid prebuilt helper archive.' >&2; fi
        else echo 'Prebuilt helper checksum mismatch.' >&2; fi
    else echo 'Prebuilt helper or checksum unavailable.' >&2; fi
    if [ -z "$helper" ]; then
        if [ "$verify_only" = 1 ]; then exit 1; fi
        if ! command -v swift >/dev/null 2>&1; then echo "No prebuilt helper for $ref; install Xcode Command Line Tools (xcode-select --install) or use a release version" >&2; exit 1; fi
        echo 'Falling back to a local Swift build.'
    else echo 'Verified prebuilt helper.'; fi
fi
if [ "$verify_only" = 1 ]; then echo 'Downloads verified.'; exit 0; fi
if [ "$no_agents" = 1 ]; then set -- --no-agents; else set --; fi
OIU_SOURCE_DIR="$work/source" OIU_MAC_HELPER_PREBUILT="$helper" sh "$work/source/setup.sh" "$@" </dev/null
printf '%s\n' "$ref" > "$install_root/VERSION-INSTALLED"
}

main "$@"
