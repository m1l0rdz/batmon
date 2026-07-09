#!/bin/bash
# Reverses install.sh. Keeps the DB unless --purge.
set -euo pipefail
PURGE="${1:-}"

launchctl bootout "gui/$(id -u)/com.dmpi.batmon-web" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.dmpi.batmon-web.plist

launchctl bootout "gui/$(id -u)/com.dmpi.batmon-ui" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.dmpi.batmon-ui.plist

sudo bash -s "$PURGE" <<'ROOT'
set -euo pipefail
PURGE="$1"
launchctl bootout system/com.dmpi.batmond 2>/dev/null || true
rm -f /Library/LaunchDaemons/com.dmpi.batmond.plist
rm -rf /usr/local/libexec/batmon
if [ "$PURGE" = "--purge" ]; then
  rm -rf /usr/local/var/batmon
  echo "database removed"
else
  echo "database kept at /usr/local/var/batmon (pass --purge to remove)"
fi
ROOT

echo "Done."
