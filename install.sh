#!/bin/bash
# batmon installer (design 5.6). Idempotent. sudo required once.
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

[ "$(uname -m)" = "arm64" ] || { echo "Apple Silicon only"; exit 1; }
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo "Do not run as root/sudo. The script uses sudo internally."; exit 1; }

# The daemon runs under the launchd interpreter (/usr/bin/python3 = Xcode
# CLT Python, often older than the dev venv). Compile batmond with THAT
# interpreter before installing so PEP 604 / walrus / f-string regressions
# fail here, not silently in a launchd crash-loop.
DAEMON_PY="/usr/bin/python3"
if [ -x "$DAEMON_PY" ]; then
  echo "== daemon syntax check ($("$DAEMON_PY" -V 2>&1)) =="
  "$DAEMON_PY" -m compileall -q batmond || {
    echo "batmond does not compile under $DAEMON_PY (the launchd interpreter)."
    echo "Fix syntax before installing - the daemon would crash-loop otherwise."
    exit 1
  }
fi

echo "== venv (web) =="
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== root install (password once) =="
sudo bash -s "$PROJECT_DIR" "$USER" <<'ROOT'
set -euo pipefail
PROJECT_DIR="$1"
CALLING_USER="$2"
mkdir -p /usr/local/var/batmon
chown root:staff /usr/local/var/batmon
chmod 0775 /usr/local/var/batmon
mkdir -p /usr/local/var/batmon/ipc
chown "$CALLING_USER":staff /usr/local/var/batmon/ipc
chmod 0700 /usr/local/var/batmon/ipc
# Root-owned code copy: the daemon must NEVER run from the user-writable
# project dir (privilege escalation - design section 7).
rm -rf /usr/local/libexec/batmon
mkdir -p /usr/local/libexec/batmon
cp -R "$PROJECT_DIR/batmond" /usr/local/libexec/batmon/batmond
chown -R root:wheel /usr/local/libexec/batmon
find /usr/local/libexec/batmon -type d -exec chmod 0755 {} +
find /usr/local/libexec/batmon -type f -exec chmod 0644 {} +
cp "$PROJECT_DIR/launchd/com.dmpi.batmond.plist" /Library/LaunchDaemons/
chown root:wheel /Library/LaunchDaemons/com.dmpi.batmond.plist
chmod 0644 /Library/LaunchDaemons/com.dmpi.batmond.plist
launchctl bootout system/com.dmpi.batmond 2>/dev/null || true
sleep 1
launchctl enable system/com.dmpi.batmond
launchctl bootstrap system /Library/LaunchDaemons/com.dmpi.batmond.plist
ROOT

echo "== user agent (web) =="
mkdir -p ~/Library/LaunchAgents
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
  launchd/com.dmpi.batmon-web.plist.template \
  > ~/Library/LaunchAgents/com.dmpi.batmon-web.plist
launchctl bootout "gui/$(id -u)/com.dmpi.batmon-web" 2>/dev/null || true
sleep 1
launchctl enable "gui/$(id -u)/com.dmpi.batmon-web"
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.dmpi.batmon-web.plist

echo "== GUI menu app (batmon-ui) =="
mkdir -p ~/Library/LaunchAgents
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
  launchd/com.dmpi.batmon-ui.plist.template \
  > ~/Library/LaunchAgents/com.dmpi.batmon-ui.plist
launchctl bootout "gui/$(id -u)/com.dmpi.batmon-ui" 2>/dev/null || true
sleep 1
launchctl enable "gui/$(id -u)/com.dmpi.batmon-ui"
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.dmpi.batmon-ui.plist

echo "Done. Dashboard: http://127.0.0.1:8899"
