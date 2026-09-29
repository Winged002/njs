#!/usr/bin/env python3
from __future__ import annotations
import json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path


def backup(path, tag):
    path=Path(path)
    if not path.exists(): return None
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dst=path.with_name(path.name+f'.bak-{tag}-{stamp}')
    shutil.copy2(path,dst)
    return dst


def main():
    if len(sys.argv)!=2:
        raise SystemExit('usage: install-syntal-ai-v392-contract.py /opt/syntal-ai/core')
    root=Path(sys.argv[1]).resolve()
    source=Path(__file__).resolve().parents[1]/'config'/'njs-v3.9.2-contract.json'
    contract=json.loads(source.read_text())
    if contract.get('njs_version')!='3.9.2' or contract.get('tool_count')!=127 or len(contract.get('tools') or [])!=127:
        raise SystemExit('Bundled NJS v3.9.2 contract is invalid')
    config=root/'config'; module=root/'app'/'njs_v37.py'; tools_module=root/'app'/'tools.py'
    if not module.exists(): raise SystemExit(f'Syntal AI source not found under {root}')
    config.mkdir(exist_ok=True)
    compat=config/'njs-v3.7-contract.json'; modern=config/'njs-v3.9.2-contract.json'; legacy39=config/'njs-v3.9-contract.json'; legacy391=config/'njs-v3.9.1-contract.json'
    for target in (compat, modern, legacy39, legacy391):
        backup(target,'before-njs-v392'); shutil.copy2(source,target)
    text=module.read_text(); marker='# NJS v3.9.2 contract compatibility installed by NJS v3.9.2'
    if marker not in text:
        backup(module,'before-njs-v392')
        text += f'''\n\n{marker}\nMANDATORY_APPROVAL_TOOLS = set(MANDATORY_APPROVAL_TOOLS) | {{\n    "njs.engagement.bulk_analyze",\n    "njs.landing_pages.delete",\n}}\nLABEL_OVERRIDES.update({{\n    "njs.engagement.overview": "Read engagement intelligence",\n    "njs.engagement.people.list": "List engagement audience",\n    "njs.engagement.queue.refresh": "Refresh Mailchimp engagement queue",\n    "njs.engagement.bulk_analyze": "Run parallel Mailchimp engagement analysis",\n    "njs.engagement.interests.list": "List engagement interests",\n    "njs.engagement.batches.list": "List engagement analysis batches",\n    "njs.engagement.batches.read": "Read engagement analysis batch",\n}})\n'''
        module.write_text(text)
    if tools_module.exists():
        text=tools_module.read_text(); changed=text
        changed=changed.replace('"njs-v3.8" if app == "njs" else','"njs-v3.9.2" if app == "njs" else')
        changed=changed.replace('"njs-v3.9.1" if app == "njs" else','"njs-v3.9.2" if app == "njs" else')
        changed=changed.replace('"njs-v3.9" if app == "njs" else','"njs-v3.9.2" if app == "njs" else')
        changed=changed.replace('"njs-v3.7" if app == "njs" else','"njs-v3.9.2" if app == "njs" else')
        if changed!=text:
            backup(tools_module,'before-njs-v392'); tools_module.write_text(changed)
    print(f'Installed NJS v3.9.2 contract with {contract["tool_count"]} tools.')
    print(f'Compatibility contract: {compat}')
    print(f'Versioned contract: {modern}')
    print('Restart Syntal AI web so the cached contract reloads.')

if __name__=='__main__': main()
