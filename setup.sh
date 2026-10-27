#!/bin/sh
# Download/run or execute from a checkout. No shell-profile edits or app bundle.
set -eu
umask 077
OIU_INSTALL_ROOT=${OIU_INSTALL_ROOT:-"$HOME/.local/share/open-iphone-use/runtime"}
OIU_BIN_DIR=${OIU_BIN_DIR:-"$HOME/.local/bin"}
OIU_REF=${OIU_REF:-main}
OIU_SOURCE_DIR=${OIU_SOURCE_DIR:-}
OIU_UNINSTALL=0
OIU_NO_AGENTS=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) OIU_UNINSTALL=1 ;;
        --no-agents) OIU_NO_AGENTS=1 ;;
        --help) echo 'Usage: sh setup.sh [--uninstall] [--no-agents]'; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done
case "$OIU_INSTALL_ROOT" in /*) ;; *) echo 'OIU_INSTALL_ROOT must be absolute' >&2; exit 2;; esac
case "$OIU_BIN_DIR" in /*) ;; *) echo 'OIU_BIN_DIR must be absolute' >&2; exit 2;; esac
if [ "$OIU_INSTALL_ROOT" = / ] || [ "$OIU_INSTALL_ROOT" = "$HOME" ] || [ -L "$OIU_INSTALL_ROOT" ]; then
    echo 'Refusing unsafe installation directory' >&2; exit 2
fi
OIU_MARKER="$OIU_INSTALL_ROOT/.open-iphone-use-managed"
OIU_PREVIOUS_CLI=
if [ "$OIU_UNINSTALL" = 1 ]; then
    if [ ! -f "$OIU_MARKER" ]; then echo 'No managed installation found; nothing removed.'; exit 0; fi
    "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use" register-agents --uninstall
    if [ -L "$OIU_BIN_DIR/open-iphone-use" ] && [ "$(readlink "$OIU_BIN_DIR/open-iphone-use")" = "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use" ]; then
        rm "$OIU_BIN_DIR/open-iphone-use"
        if [ -f "$OIU_INSTALL_ROOT/previous-cli-link" ]; then ln -s "$(cat "$OIU_INSTALL_ROOT/previous-cli-link")" "$OIU_BIN_DIR/open-iphone-use"; fi
    fi
    rm -rf "$OIU_INSTALL_ROOT"
    echo 'Uninstalled managed runtime, CLI, and unchanged agent entries. Device state, CA, backups and user edits were preserved.'
    exit 0
fi
case "$(uname -s)" in
    Darwin) OIU_OS=macos ;;
    Linux) OIU_OS=linux ;;
    *) echo 'Use setup.ps1 on Windows' >&2; exit 2 ;;
esac
if [ -e "$OIU_INSTALL_ROOT" ] && [ ! -f "$OIU_MARKER" ]; then
    echo "Refusing to overwrite unmanaged directory: $OIU_INSTALL_ROOT" >&2; exit 1
fi
# Reject an occupied CLI before downloading or modifying an existing installation.
if [ -e "$OIU_BIN_DIR/open-iphone-use" ] || [ -L "$OIU_BIN_DIR/open-iphone-use" ]; then
    if [ ! -L "$OIU_BIN_DIR/open-iphone-use" ] || [ "$(readlink "$OIU_BIN_DIR/open-iphone-use")" != "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use" ]; then
        if [ -L "$OIU_BIN_DIR/open-iphone-use" ]; then
            case "$(readlink "$OIU_BIN_DIR/open-iphone-use")" in
                *"Open iPhone Use.app/Contents/Helpers/open-iphone-use") OIU_PREVIOUS_CLI=$(readlink "$OIU_BIN_DIR/open-iphone-use") ;;
                *) echo "CLI path occupied by another installation; choose OIU_BIN_DIR" >&2; exit 1 ;;
            esac
        else
        echo "CLI path already occupied: $OIU_BIN_DIR/open-iphone-use. Choose another OIU_BIN_DIR." >&2; exit 1
        fi
    fi
fi
mkdir -p "$OIU_INSTALL_ROOT" "$OIU_BIN_DIR"
: > "$OIU_MARKER"
if [ -n "$OIU_PREVIOUS_CLI" ] && [ ! -f "$OIU_INSTALL_ROOT/previous-cli-link" ]; then printf '%s\n' "$OIU_PREVIOUS_CLI" > "$OIU_INSTALL_ROOT/previous-cli-link"; fi
OIU_TMP=$(mktemp -d "${TMPDIR:-/tmp}/oiu-setup.XXXXXX")
trap 'rm -rf "$OIU_TMP"' EXIT HUP INT TERM
# Prefer the checkout containing this script; piped invocation falls back to the chosen ref.
if [ -z "$OIU_SOURCE_DIR" ] && [ -f "$0" ]; then
    OIU_SCRIPT_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    if [ -f "$OIU_SCRIPT_ROOT/server/pyproject.toml" ]; then OIU_SOURCE_DIR=$OIU_SCRIPT_ROOT; fi
fi
if [ -z "$OIU_SOURCE_DIR" ]; then
    command -v curl >/dev/null 2>&1 || { echo 'Install curl, then re-run setup' >&2; exit 1; }
    curl -fLsS "https://github.com/semsturgut/open-iphone-use/archive/$OIU_REF.tar.gz" -o "$OIU_TMP/source.tar.gz"
    mkdir "$OIU_TMP/source"
    tar -xzf "$OIU_TMP/source.tar.gz" -C "$OIU_TMP/source" --strip-components=1
    OIU_SOURCE_DIR="$OIU_TMP/source"
fi
[ -f "$OIU_SOURCE_DIR/server/pyproject.toml" ] || { echo 'Invalid source directory' >&2; exit 1; }
OIU_PYTHON=${OIU_PYTHON:-python3}
if ! command -v "$OIU_PYTHON" >/dev/null 2>&1 || ! "$OIU_PYTHON" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
    # Use an isolated managed CPython when the host has an older runtime.
    if command -v uv >/dev/null 2>&1; then OIU_UV=$(command -v uv)
    else
        curl -fLsS https://astral.sh/uv/install.sh -o "$OIU_TMP/uv-install.sh"
        UV_INSTALL_DIR="$OIU_INSTALL_ROOT/uv" UV_NO_MODIFY_PATH=1 sh "$OIU_TMP/uv-install.sh"
        OIU_UV="$OIU_INSTALL_ROOT/uv/uv"
    fi
    UV_PYTHON_INSTALL_DIR="$OIU_INSTALL_ROOT/python" "$OIU_UV" python install 3.12
    OIU_PYTHON=$(UV_PYTHON_INSTALL_DIR="$OIU_INSTALL_ROOT/python" "$OIU_UV" python find --managed-python 3.12)
fi
if [ "$OIU_OS" = macos ]; then
    if [ -n "${OIU_MAC_HELPER_PREBUILT:-}" ] && [ -f "$OIU_MAC_HELPER_PREBUILT" ] && [ -x "$OIU_MAC_HELPER_PREBUILT" ]; then
        OIU_NATIVE_HELPER=$OIU_MAC_HELPER_PREBUILT
    else
        command -v swift >/dev/null 2>&1 || { echo 'Install Xcode/Swift 6 (xcode-select --install), then re-run setup.' >&2; exit 1; }
        # Xcode/SDK installation and its license approval remain owner actions.
        if [ "${OIU_SWIFT_DISABLE_SANDBOX:-0}" = 1 ]; then
            swift build --package-path "$OIU_SOURCE_DIR/macos" -c release --product open-iphone-use-headless --disable-sandbox --cache-path "$OIU_TMP/swift-cache"
            OIU_NATIVE_DIR=$(swift build --package-path "$OIU_SOURCE_DIR/macos" -c release --show-bin-path --disable-sandbox --cache-path "$OIU_TMP/swift-cache")
        else
            swift build --package-path "$OIU_SOURCE_DIR/macos" -c release --product open-iphone-use-headless
            OIU_NATIVE_DIR=$(swift build --package-path "$OIU_SOURCE_DIR/macos" -c release --show-bin-path)
        fi
        OIU_NATIVE_HELPER="$OIU_NATIVE_DIR/open-iphone-use-headless"
    fi
else
    if ! command -v bluetoothctl >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then sudo apt-get update; sudo apt-get install -y bluez dbus
        elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y bluez dbus
        elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --needed bluez bluez-utils dbus
        else echo 'Install BlueZ and D-Bus using your OS package manager, then re-run setup.' >&2; exit 1
        fi
    fi
    if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet bluetooth; then sudo systemctl enable --now bluetooth; fi
fi
"$OIU_PYTHON" -m venv "$OIU_INSTALL_ROOT/venv"
"$OIU_INSTALL_ROOT/venv/bin/python" -m pip install --upgrade pip
"$OIU_INSTALL_ROOT/venv/bin/python" -m pip install --upgrade "$OIU_SOURCE_DIR/server"
if [ "$OIU_OS" = macos ]; then
    cp "$OIU_NATIVE_HELPER" "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use-headless.new"
    chmod 755 "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use-headless.new"
    mv "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use-headless.new" "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use-headless"
fi
cp "$OIU_SOURCE_DIR/setup.sh" "$OIU_INSTALL_ROOT/setup.sh"
cp "$OIU_SOURCE_DIR/macos/Resources/open-iphone-use-skill/SKILL.md" "$OIU_INSTALL_ROOT/agent-skill.md"
# Remove the old template name; registration installs only the canonical skill.
rm -f "$OIU_INSTALL_ROOT/SKILL.md"
ln -sfn "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use" "$OIU_BIN_DIR/open-iphone-use"
if [ "$OIU_NO_AGENTS" = 0 ]; then
    "$OIU_INSTALL_ROOT/venv/bin/open-iphone-use" register-agents --skill "$OIU_INSTALL_ROOT/agent-skill.md"
fi
printf '\nInstalled. Start now:\n  %s/open-iphone-use serve\n' "$OIU_BIN_DIR"
case ":$PATH:" in *":$OIU_BIN_DIR:"*) ;; *) printf 'Add %s to PATH to use open-iphone-use from any terminal.\n' "$OIU_BIN_DIR" ;; esac
