#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$SCRIPT_DIR/../VERSION")"
ARCH="$(uname -m)"
OUTPUT="${1:-$SCRIPT_DIR/../dist}"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
STAGING="$(mktemp -d /tmp/open-iphone-use-dmg.XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
"$SCRIPT_DIR/build-app.sh" "$STAGING/Open iPhone Use.app"
cp "$SCRIPT_DIR/scripts/install-open-iphone-use.command" "$STAGING/Install Open iPhone Use.command"
chmod 755 "$STAGING/Install Open iPhone Use.command"
ACTUAL="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGING/Open iPhone Use.app/Contents/Info.plist")"
[[ "$ACTUAL" == "$VERSION" ]] || { echo "App version differs from VERSION" >&2; exit 1; }
ln -s /Applications "$STAGING/Applications"
DMG="$OUTPUT/Open-iPhone-Use-$VERSION-macos-$ARCH.dmg"
hdiutil create -volname "Open iPhone Use $VERSION" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
hdiutil verify "$DMG"
(cd "$OUTPUT" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "Created $DMG"
