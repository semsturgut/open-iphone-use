#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="${1:-$HOME/Applications/Open iPhone Use.app}"
SIGNING_IDENTITY="${OPEN_IPHONE_USE_CODESIGN_IDENTITY:--}"
case "$APP_PATH" in
  /*.app) ;;
  *) echo "Choose an absolute destination ending in .app." >&2; exit 2 ;;
esac
if [[ "$APP_PATH" == "/" || "$APP_PATH" == "$HOME" || -L "$APP_PATH" ]]; then
  echo "Refusing an unsafe app destination: $APP_PATH" >&2
  exit 2
fi
APP_MACOS="$APP_PATH/Contents/MacOS"
APP_HELPERS="$APP_PATH/Contents/Helpers"
APP_RESOURCES="$APP_PATH/Contents/Resources"

cd "$SCRIPT_DIR"
swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"

if [[ -e "$APP_PATH" ]]; then
  if [[ ! -f "$APP_PATH/Contents/Info.plist" ]]; then
    echo "Refusing to replace a directory that is not a Open iPhone Use app bundle: $APP_PATH" >&2
    exit 2
  fi
  EXISTING_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP_PATH/Contents/Info.plist" 2>/dev/null || true)"
  if [[ "$EXISTING_ID" != "dev.openiphoneuse.mac" ]]; then
    echo "Refusing to replace app bundle ID '$EXISTING_ID' at $APP_PATH." >&2
    exit 2
  fi
  rm -rf "$APP_PATH"
fi
mkdir -p "$APP_MACOS" "$APP_HELPERS" "$APP_RESOURCES"
cp "$BIN_DIR/OpenIPhoneUseMac" "$APP_MACOS/OpenIPhoneUseMac"
cp "$BIN_DIR/open-iphone-use-mcp" "$APP_HELPERS/open-iphone-use-mcp"
cp Sources/OpenIPhoneUseMac/Info.plist "$APP_PATH/Contents/Info.plist"
cp Resources/AppIcon.icns "$APP_RESOURCES/AppIcon.icns"

RESOURCE_BUNDLE="$(find "$BIN_DIR" -maxdepth 1 -type d -name 'OpenIPhoneUseMac_OpenIPhoneUseCore.bundle' -print -quit)"
if [[ -z "$RESOURCE_BUNDLE" ]]; then
  echo "Swift package capture resource bundle was not built." >&2
  exit 1
fi
cp -R "$RESOURCE_BUNDLE" "$APP_RESOURCES/"
chmod 755 "$APP_MACOS/OpenIPhoneUseMac" "$APP_HELPERS/open-iphone-use-mcp"
xattr -cr "$APP_PATH"
codesign --force --deep --sign "$SIGNING_IDENTITY" --identifier dev.openiphoneuse.mac "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"
echo "Built: $APP_PATH"
