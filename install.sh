#!/usr/bin/env bash
# Bootstrap a managed installation without a checkout.
set -eu

usage() {
    cat <<'USAGE'
Usage: bash install.sh [--version <tag|branch>] [--no-agents] [--uninstall] [--dry-run]
USAGE
}

main() {
ref= no_agents=0 uninstall=0 dry_run=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version) [ "$#" -ge 2 ] && [ -n "$2" ] || { echo '--version requires a ref' >&2; exit 2; }; ref=$2; shift ;;
        --no-agents) no_agents=1 ;;
        --uninstall) uninstall=1 ;;
        --dry-run) dry_run=1 ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
repo=${OIU_REPO:-semsturgut/open-iphone-use}
install_root=${OIU_INSTALL_ROOT:-"$HOME/.local/share/open-iphone-use/runtime"}
bin_dir=${OIU_BIN_DIR:-"$HOME/.local/bin"}
case "$install_root" in /*) ;; *) echo 'OIU_INSTALL_ROOT must be absolute' >&2; exit 2;; esac
case "$bin_dir" in /*) ;; *) echo 'OIU_BIN_DIR must be absolute' >&2; exit 2;; esac
os=${OIU_UNAME:-$(uname -s)}
[ "$os" = Darwin ] || { echo "Open iPhone Use runs on macOS (it needs Xcode 27); this is $os." >&2; exit 1; }
if [ "$uninstall" = 1 ]; then
    if [ "$dry_run" = 1 ]; then printf 'Plan: uninstall using %s/setup.sh\n' "$install_root"; exit 0; fi
    if [ -f "$install_root/setup.sh" ] && [ -f "$install_root/.open-iphone-use-managed" ]; then
        exec sh "$install_root/setup.sh" --uninstall
    fi
    if [ ! -f "$install_root/.open-iphone-use-managed" ]; then echo 'No managed installation found; nothing removed.'; exit 0; fi
    echo "Installed setup.sh not found in $install_root; use the setup.sh from your original checkout with --uninstall." >&2; exit 1
fi
for cmd in curl tar; do command -v "$cmd" >/dev/null 2>&1 || { echo "Install $cmd, then re-run install.sh." >&2; exit 1; }; done
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
printf 'Ref: %s\nSource: %s\n' "$ref" "$source_url"
[ -n "${OIU_ARCHIVE_URL:-}" ] || printf 'Source fallback: %s\n' "$fallback_url"
printf 'Install root: %s\nCLI directory: %s\n' "$install_root" "$bin_dir"
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
if [ "$no_agents" = 1 ]; then set -- --no-agents; else set --; fi
OIU_SOURCE_DIR="$work/source" sh "$work/source/setup.sh" "$@" </dev/null
printf '%s\n' "$ref" > "$install_root/VERSION-INSTALLED"
}

main "$@"
