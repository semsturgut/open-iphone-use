#!/bin/bash
set -euo pipefail

APP_PATH=""
for candidate in "/Applications/Open iPhone Use.app" "$HOME/Applications/Open iPhone Use.app"; do
  if [[ -d "$candidate/Contents/Helpers" ]]; then
    APP_PATH="$candidate"
    break
  fi
done

if [[ -z "$APP_PATH" ]]; then
  echo "Move Open iPhone Use.app from this disk image into Applications, then run this command again."
  read -r -n 1 -s -p "Press any key to close..."
  echo
  exit 1
fi

"$APP_PATH/Contents/Helpers/open-iphone-use" install
echo
echo "Open iPhone Use CLI and detected MCP clients are ready. Restart OpenCode and DeepSeek Harness if they were already open."
read -r -n 1 -s -p "Press any key to close..."
echo
