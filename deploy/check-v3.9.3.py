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

    config_py = (root / "config.py").read_text()
    compose = (root / "compose.yml").read_text()
    require('"3.9.3"' in config_py, "config.py does not declare 3.9.3")
    require("APP_VERSION: 3.9.3" in compose, "compose.yml does not declare 3.9.3")

    contract = json.loads((root / "config" / "njs-v3.9.3-contract.json").read_text())
    require(contract.get("njs_version") == "3.9.3", "contract version mismatch")
    require(contract.get("tool_count") == 127 and len(contract.get("tools") or []) == 127, "expected 127 AI tools")
    by_name = {row.get("name"): row for row in contract.get("tools") or []}
    update_tool = by_name.get("njs.newsletters.editions.update") or {}
    props = ((update_tool.get("input_schema") or {}).get("properties") or {})
    require("visual_prompt" in props, "newsletter edition update tool missing visual_prompt")
    require("apply_to_future" in props, "newsletter edition update tool missing apply_to_future")

    db_text = (root / "db.py").read_text()
    require("newsletter_edition_versions" in db_text, "newsletter edition version collection missing")
    require("newsletter_design_jobs" in db_text, "newsletter design job collection missing")

    service = (root / "newsletter_service.py").read_text()
    for marker in ("apply_newsletter_visual_prompt", "compile_newsletter_designed_html", "sanitize_newsletter_design_html"):
        require(marker in service, f"newsletter design service missing {marker}")

    tasks = (root / "tasks.py").read_text()
    require("newsletter.apply_visual_prompt" in tasks, "newsletter visual design Celery task missing")

    app = (root / "app.py").read_text()
    for marker in ("newsletter_edition_design_ai", "newsletter_edition_design_html_save", "newsletter_edition_design_restore", "newsletter_edition_design_job_status"):
        require(marker in app, f"newsletter design route missing {marker}")

    template = (root / "templates" / "newsletter_edition.html").read_text()
    for marker in ("Visual design", "Apply visual changes with AI", "Use this direction for future editions", "Advanced HTML source", "data-email-stage"):
        require(marker in template, f"Newsletter Visual Design UI missing: {marker}")

    css = (root / "static" / "app.css").read_text()
    require("NJS v3.9.3 — Prompt-driven Newsletter Design Studio" in css, "v3.9.3 design CSS marker missing")

    for path in ("app.py", "tasks.py", "db.py", "newsletter_service.py", "ai_control.py", "ai_control_ext.py", "config.py"):
        compile((root / path).read_text(), str(root / path), "exec")

    print("PASS: NJS v3.9.3 Prompt Newsletter Design Studio release checks passed")
    print("AI tools: 127")
    print("Newsletter visual editing: prompt + revision history + HTML fallback")
    print("Future editions: standing visual design prompt supported")


if __name__ == "__main__":
    main()
