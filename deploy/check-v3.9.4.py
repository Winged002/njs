#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    require(root.exists(), f"release root does not exist: {root}")

    config_py = (root / "config.py").read_text()
    compose = (root / "compose.yml").read_text()
    require('"3.9.4"' in config_py, "config.py does not declare 3.9.4")
    require("APP_VERSION: 3.9.4" in compose, "compose.yml does not declare 3.9.4")
    require("syntal-njs-app:3.9.4" in compose, "compose image tag does not declare 3.9.4")

    contract = json.loads((root / "config" / "njs-v3.9.4-contract.json").read_text())
    manifest = json.loads((root / "NJS-AI-MANIFEST-v3.9.4.json").read_text())
    require(contract.get("njs_version") == "3.9.4", "contract version mismatch")
    require(manifest.get("app_version") == "3.9.4", "manifest version mismatch")
    require(contract.get("tool_count") == 127 and len(contract.get("tools") or []) == 127, "expected 127 contract tools")
    require(manifest.get("tool_count") == 127 and len(manifest.get("tools") or []) == 127, "expected 127 manifest tools")

    by_name = {row.get("name"): row for row in contract.get("tools") or []}
    people_tool = by_name.get("njs.engagement.people.list") or {}
    people_enum = (((people_tool.get("input_schema") or {}).get("properties") or {}).get("bucket") or {}).get("enum") or []
    require("failed" in people_enum, "engagement people tool does not expose failed state")
    bulk_tool = by_name.get("njs.engagement.bulk_analyze") or {}
    max_items = ((((bulk_tool.get("input_schema") or {}).get("properties") or {}).get("person_ids") or {}).get("maxItems"))
    require(max_items == 2500, "engagement bulk tool is not capped at 2500")

    service = (root / "blackbook_service.py").read_text()
    require("min(2500" in service, "NJS BlackBook client does not allow 2500 engagement people")
    require("len(ids) > 2500" in service, "NJS BlackBook client does not validate the 2500 batch limit")

    app = (root / "app.py").read_text()
    require('"failed", "inactive", "low", "medium", "high", "all"' in app, "NJS engagement route does not expose failed")
    require("len(person_ids) > 2500" in app, "NJS form validation does not allow 2500")
    require("people_limit = max(1, min(2500" in app, "NJS UI does not request up to 2500 rows")

    ai = (root / "ai_control.py").read_text()
    require('"queue","failed","inactive","low","medium","high","all"' in ai, "AI endpoint does not expose failed")
    require("maximum=2500" in ai and "len(person_ids) > 2500" in ai, "AI endpoint does not enforce 2500")

    bridge = (root / "deploy" / "blackbook-engagement-intelligence-v4.inc.py").read_text()
    for marker in (
        "NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V4",
        'NJS_ENGAGEMENT_MAX_SELECT", "2500"',
        '"marketing.engagement_intelligence.bucket": "failed"',
        'return "mailchimp_access_denied"',
        'inc["counts.failed"] = 1',
        'for bucket in ("failed", "inactive", "low", "medium", "high")',
        'bucket in {"failed", "inactive", "low", "medium", "high"}',
    ):
        require(marker in bridge, f"BlackBook v4 bridge missing marker: {marker}")

    installer = (root / "deploy" / "install-blackbook-engagement-intelligence-v4.py").read_text()
    require("START_V4" in installer and "END_V4" in installer, "v4 BlackBook installer markers missing")
    require("NJS_ENGAGEMENT_MAX_SELECT=2500" in installer, "v4 installer does not enforce 2500 in BlackBook env")

    template = (root / "templates" / "newsletter_engagement.html").read_text()
    for marker in ("Failed", "Retry selected failed", "Max select {{ max_select }}", "Math.min(2500"):
        require(marker in template, f"Engagement UI missing: {marker}")

    # v3.9.3 visual newsletter design must remain intact.
    newsletter_template = (root / "templates" / "newsletter_edition.html").read_text()
    require("Apply visual changes with AI" in newsletter_template, "v3.9.3 prompt newsletter design regressed")

    for path in (
        "app.py", "tasks.py", "db.py", "newsletter_service.py", "blackbook_service.py",
        "ai_control.py", "ai_control_ext.py", "config.py",
        "deploy/blackbook-engagement-intelligence-v4.inc.py",
        "deploy/install-blackbook-engagement-intelligence-v4.py",
        "deploy/install-syntal-ai-v394-contract.py",
    ):
        compile((root / path).read_text(), str(root / path), "exec")

    env = Environment(loader=FileSystemLoader(str(root / "templates")))
    env.filters["dt"] = lambda value: value
    env.get_template("newsletter_engagement.html")
    env.get_template("newsletter_edition.html")

    print("PASS: NJS v3.9.4 Failed Engagement Queue release checks passed")
    print("AI tools: 127")
    print("Bulk engagement limit: 2500")
    print("Per-contact failures: terminal Failed state; batch continues")
    print("Mailchimp 403 normalization: mailchimp_access_denied")


if __name__ == "__main__":
    main()
