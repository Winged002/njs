#!/usr/bin/env python3
"""Install/update the NJS v3.9.2 dynamic newsletter audience bridge in BlackBook.

Usage:
    python3 deploy/install-blackbook-newsletter-bridge-v2.py /opt/blackbook/core/app.py
"""
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

START_V2 = "# NJS_NEWSLETTER_BRIDGE_V2"
END_V2 = "# END_NJS_NEWSLETTER_BRIDGE_V2"
START_V1 = "# NJS_NEWSLETTER_BRIDGE_V1"
END_V1 = "# END_NJS_NEWSLETTER_BRIDGE_V1"
ERROR_MARKER = '@app.errorhandler(400)'


def backup(path: Path):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dest = path.with_name(f"{path.name}.bak-before-njs-newsletter-v2-{stamp}")
    shutil.copy2(path, dest)
    return dest


def replace_block(source: str, block: str):
    for start, end in ((START_V2, END_V2), (START_V1, END_V1)):
        if start in source:
            a = source.index(start)
            b = source.find(end, a)
            if b < 0:
                raise SystemExit(f"Existing {start} found without {end}")
            b += len(end)
            if b < len(source) and source[b:b+1] == "\n":
                b += 1
            return source[:a] + block + source[b:], "updated"
    if ERROR_MARKER not in source:
        raise SystemExit("Could not find BlackBook error-handler insertion marker")
    return source.replace(ERROR_MARKER, block + ERROR_MARKER, 1), "installed"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: install-blackbook-newsletter-bridge-v2.py /opt/blackbook/core/app.py")
    app_path = Path(sys.argv[1]).resolve()
    bridge_path = Path(__file__).with_name("blackbook-newsletter-bridge-v2.inc.py")
    if not app_path.exists() or not bridge_path.exists():
        raise SystemExit("BlackBook app.py or newsletter bridge v2 include is missing")
    block = bridge_path.read_text().rstrip() + "\n" + END_V2 + "\n\n"
    source, action = replace_block(app_path.read_text(), block)
    backup_path = backup(app_path)
    app_path.write_text(source)
    print(f"NJS Newsletter Studio BlackBook bridge v2 {action}: {app_path}")
    print(f"Backup: {backup_path}")
    print("Dynamic audience rules enabled: engagement buckets + canonical interests (ANY/ALL).")


if __name__ == '__main__':
    main()
