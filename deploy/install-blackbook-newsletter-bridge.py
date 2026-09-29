#!/usr/bin/env python3
"""Install/update the NJS Newsletter Studio marketing bridge in BlackBook app.py.

Usage:
    python3 deploy/install-blackbook-newsletter-bridge.py /opt/blackbook/core/app.py
"""
from pathlib import Path
import sys

MARKER_START = "# NJS_NEWSLETTER_BRIDGE_V1"
MARKER_END = "# END_NJS_NEWSLETTER_BRIDGE_V1"
ERROR_MARKER = '@app.errorhandler(400)'

if len(sys.argv) != 2:
    raise SystemExit("Usage: install-blackbook-newsletter-bridge.py /path/to/blackbook/app.py")
app_path = Path(sys.argv[1]).resolve()
bridge_path = Path(__file__).with_name("blackbook-newsletter-bridge-v1.inc.py")
source = app_path.read_text()
bridge = bridge_path.read_text().rstrip() + "\n" + MARKER_END + "\n\n"
if ERROR_MARKER not in source:
    raise SystemExit("Could not find BlackBook error-handler insertion marker")
if MARKER_START in source:
    start = source.index(MARKER_START)
    end = source.find(MARKER_END, start)
    if end < 0:
        raise SystemExit("Existing bridge start marker found without end marker")
    end = source.find("\n", end)
    source = source[:start] + bridge + source[end+1:]
    action = "updated"
else:
    source = source.replace(ERROR_MARKER, bridge + ERROR_MARKER, 1)
    action = "installed"
backup = app_path.with_name(app_path.name + ".bak-njs-newsletter-bridge")
if not backup.exists():
    backup.write_text(app_path.read_text())
app_path.write_text(source)
print(f"NJS Newsletter Studio BlackBook bridge {action}: {app_path}")
