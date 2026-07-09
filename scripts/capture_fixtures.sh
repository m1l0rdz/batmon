#!/bin/bash
# Capture parser fixtures on the target machine (MacBook Pro M4 Pro).
# powermetrics needs sudo. Run from repo root: bash scripts/capture_fixtures.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures"
mkdir -p "$DIR"

echo "== ioreg AppleSmartBattery =="
ioreg -rn AppleSmartBattery -a > "$DIR/ioreg_battery.plist"

echo "== pmset assertions =="
pmset -g assertions > "$DIR/pmset_assertions.txt"

echo "== brightness (optional, may fail) =="
/usr/libexec/corebrightnessdiag status-info > "$DIR/corebrightnessdiag.txt" \
  || echo "corebrightnessdiag failed - brightness will be NULL, acceptable"

echo "== powermetrics burst (sudo) =="
sudo powermetrics --samplers tasks,cpu_power,gpu_power,thermal \
  --show-process-energy --show-process-gpu \
  -i 1000 -n 5 --format plist > "$DIR/powermetrics_burst.plist"

echo "Done: $DIR"
echo "ALSO capture two ioreg variants manually:"
echo "  plugged in : ioreg -rn AppleSmartBattery -a > $DIR/ioreg_battery_charging.plist"
echo "  unplugged  : ioreg -rn AppleSmartBattery -a > $DIR/ioreg_battery_discharging.plist"
