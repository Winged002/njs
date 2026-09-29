#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def backup(path: Path, tag: str):
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(path.name + f".bak-{tag}-{stamp}")
    shutil.copy2(path, dest)
    return dest


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: install-syntal-ai-v38-contract.py /opt/syntal-ai/core")
    ai_root = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1] / "config" / "njs-v3.8-contract.json"
    if not source.exists():
        raise SystemExit(f"Missing bundled contract: {source}")
    contract = json.loads(source.read_text())
    if contract.get("application") != "njs" or contract.get("njs_version") != "3.8.0":
        raise SystemExit("Bundled contract is not the NJS v3.8.0 contract")
    if int(contract.get("tool_count") or 0) != len(contract.get("tools") or []):
        raise SystemExit("Bundled contract tool_count does not match tools array")

    config_dir = ai_root / "config"
    module = ai_root / "app" / "njs_v37.py"
    tools_module = ai_root / "app" / "tools.py"
    if not config_dir.exists() or not module.exists():
        raise SystemExit(f"Syntal AI source was not found under {ai_root}")

    legacy = config_dir / "njs-v3.7-contract.json"
    modern = config_dir / "njs-v3.8-contract.json"
    old_backup = backup(legacy, "before-njs-v38")
    modern_backup = backup(modern, "before-njs-v38")
    shutil.copy2(source, legacy)
    shutil.copy2(source, modern)

    text = module.read_text()
    marker = "# NJS v3.8 contract compatibility installed by NJS v3.8.0"
    if marker not in text:
        text += f'''\n\n{marker}\nMANDATORY_APPROVAL_TOOLS = set(MANDATORY_APPROVAL_TOOLS) | {{\n    "njs.landing_pages.delete",\n}}\nLABEL_OVERRIDES.update({{\n    "njs.landing_pages.create_manual": "Create landing page manually",\n    "njs.landing_pages.create_ai": "Create landing page with AI",\n    "njs.landing_pages.html.write": "Write landing page HTML",\n    "njs.landing_pages.ai.revise": "Revise landing page with AI",\n    "njs.landing_pages.publish": "Publish landing page",\n    "njs.landing_pages.unpublish": "Unpublish landing page",\n    "njs.landing_pages.delete": "Delete landing page",\n}})\n'''
        backup(module, "before-njs-v38")
        module.write_text(text)

    if tools_module.exists():
        tools_text = tools_module.read_text()
        old = '"njs-v3.7" if app == "njs" else'
        new = '"njs-v3.8" if app == "njs" else'
        if old in tools_text:
            backup(tools_module, "before-njs-v38")
            tools_module.write_text(tools_text.replace(old, new, 1))

    print(f"Installed NJS v3.8.0 contract with {contract['tool_count']} tools.")
    print(f"Compatibility contract: {legacy}")
    print(f"Versioned contract: {modern}")
    if old_backup:
        print(f"Backup: {old_backup}")
    if modern_backup:
        print(f"Backup: {modern_backup}")
    print("Restart the Syntal AI web service so the cached contract reloads.")


if __name__ == "__main__":
    main()
