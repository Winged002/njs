#!/usr/bin/env python3
from pathlib import Path
import shutil, sys
from datetime import datetime, timezone

START_V2 = "# NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V2"
START_V4 = "# NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V4"
END_V4 = "# END_NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V4"
END_V2 = "# END_NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V2"
START_V3 = "# NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V3"
END_V3 = "# END_NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V3"
ANCHOR = '@app.errorhandler(400)'
CSRF_MARKER = "# NJS_MARKETING_BRIDGE_CSRF_EXEMPT_V1"
CSRF_ENDPOINTS = [
    "njs_marketing_audience_preview_api",
    "njs_marketing_newsletter_distribute_api",
    "njs_engagement_queue_refresh_api",
    "njs_engagement_bulk_analyze_api",
]


def backup(path: Path, label: str):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dest = path.with_name(f"{path.name}.bak-{label}-{stamp}")
    shutil.copy2(path, dest)
    return dest


def replace_bridge(source: str, block: str):
    for start, end in ((START_V4, END_V4), (START_V3, END_V3), (START_V2, END_V2)):
        if start in source:
            a = source.index(start)
            b = source.index(end, a) + len(end)
            return source[:a] + block.rstrip() + source[b:]
    if ANCHOR not in source:
        raise SystemExit("BlackBook error-handler insertion point not found")
    return source.replace(ANCHOR, block + ANCHOR, 1)


def patch_csrf(source: str):
    # If our earlier hotfix exists, make sure all current endpoints remain present.
    if CSRF_MARKER in source:
        start = source.index(CSRF_MARKER)
        tail = source[start:start+2000]
        missing = [name for name in CSRF_ENDPOINTS if name not in tail]
        if not missing:
            return source
    anchor = '    if request.path.startswith("/api/ai/v1/"):\n        return\n'
    if anchor not in source:
        raise SystemExit("Could not find csrf_protect() AI exemption anchor")
    block = anchor + '''\n    # NJS_MARKETING_BRIDGE_CSRF_EXEMPT_V1\n    # Authenticated machine-to-machine bridge endpoints do not use browser CSRF.\n    if request.endpoint in {\n        "njs_marketing_audience_preview_api",\n        "njs_marketing_newsletter_distribute_api",\n        "njs_engagement_queue_refresh_api",\n        "njs_engagement_bulk_analyze_api",\n    }:\n        return\n'''
    # Remove an older marker block if present before inserting a canonical one.
    if CSRF_MARKER in source:
        marker_pos = source.index(CSRF_MARKER)
        block_start = source.rfind("    #", 0, marker_pos + 1)
        next_mailchimp = source.find("    # Mailchimp webhooks", marker_pos)
        if block_start >= 0 and next_mailchimp > marker_pos:
            source = source[:block_start] + source[next_mailchimp:]
    return source.replace(anchor, block, 1)


def patch_compose(path: Path):
    if not path.exists():
        return None
    s = path.read_text()
    old = '--concurrency=2'
    new = '--concurrency="$${BLACKBOOK_WORKER_CONCURRENCY:-6}"'
    if old in s:
        b = backup(path, "before-parallel-engagement")
        path.write_text(s.replace(old, new, 1))
        return b
    return None



def patch_env(path: Path):
    if not path.exists():
        return None
    source = path.read_text()
    lines = source.splitlines()
    changed = False
    found = False
    out = []
    for line in lines:
        if line.strip().startswith("NJS_ENGAGEMENT_MAX_SELECT="):
            found = True
            if line.strip() != "NJS_ENGAGEMENT_MAX_SELECT=2500":
                line = "NJS_ENGAGEMENT_MAX_SELECT=2500"
                changed = True
        out.append(line)
    if not found:
        out.append("NJS_ENGAGEMENT_MAX_SELECT=2500")
        changed = True
    if not changed:
        return None
    b = backup(path, "before-njs-engagement-v4-limit")
    path.write_text("\n".join(out).rstrip() + "\n")
    return b

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: install-blackbook-engagement-intelligence-v4.py /opt/blackbook/core")
    root = Path(sys.argv[1]).resolve()
    app = root / "app.py"
    inc = Path(__file__).with_name("blackbook-engagement-intelligence-v4.inc.py")
    if not app.exists() or not inc.exists():
        raise SystemExit("BlackBook app.py or v4 bridge include is missing")

    source = app.read_text()
    block = inc.read_text().strip() + "\n\n"
    source = replace_bridge(source, block)
    source = patch_csrf(source)
    app_backup = backup(app, "before-njs-engagement-v4")
    app.write_text(source)

    compose = root / "docker-compose.yml"
    compose_backup = patch_compose(compose)
    env_backup = patch_env(root / ".env")

    print(f"Installed NJS Engagement Intelligence v4 bridge into {app}")
    print(f"App backup: {app_backup}")
    if compose_backup:
        print(f"Compose backup: {compose_backup}")
    if env_backup:
        print(f"Environment backup: {env_backup}")
    print("Parallel worker default: BLACKBOOK_WORKER_CONCURRENCY=6")
    print("Bulk selection maximum: NJS_ENGAGEMENT_MAX_SELECT=2500")
    print("Per-person campaign import default: NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT=20")


if __name__ == '__main__':
    main()
