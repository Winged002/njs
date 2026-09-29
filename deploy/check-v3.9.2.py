#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    require(root.exists(), f"release root does not exist: {root}")
    config = (root / "config.py").read_text()
    require('"3.9.2"' in config, "config.py does not declare 3.9.2")
    contract_path = root / "config" / "njs-v3.9.2-contract.json"
    contract = json.loads(contract_path.read_text())
    require(contract.get("njs_version") == "3.9.2", "contract version mismatch")
    require(contract.get("tool_count") == 127 and len(contract.get("tools") or []) == 127, "expected 127 AI tools")
    by_name = {row.get("name"): row for row in contract.get("tools") or []}
    for name in ("njs.blackbook.audience.preview", "njs.newsletters.audience.update"):
        require(name in by_name, f"missing tool {name}")
        props = ((by_name[name].get("input_schema") or {}).get("properties") or {})
        for prop in ("engagement_buckets", "interest_ids", "interest_match"):
            require(prop in props, f"{name} missing {prop}")
    template = (root / "templates" / "newsletter_audience.html").read_text()
    for marker in ("engagement_buckets", "interest_ids", "interest_match", "data-preview-url"):
        require(marker in template, f"Audience UI missing {marker}")
    bridge = (root / "deploy" / "blackbook-newsletter-bridge-v2.inc.py").read_text()
    require("NJS_NEWSLETTER_BRIDGE_V2" in bridge, "BlackBook bridge v2 marker missing")
    require("marketing_person_interests" in bridge, "BlackBook bridge is not resolving canonical interests")
    compile(bridge, str(root / "deploy" / "blackbook-newsletter-bridge-v2.inc.py"), "exec")
    for path in ("app.py", "blackbook_service.py", "newsletter_service.py", "ai_control.py", "ai_control_ext.py"):
        compile((root / path).read_text(), str(root / path), "exec")
    print("PASS: NJS v3.9.2 Smart Audience Builder release checks passed")
    print("AI tools: 127")
    print("BlackBook bridge: v2")
    print("Audience criteria: engagement buckets + canonical interests + ANY/ALL")


if __name__ == "__main__":
    main()
