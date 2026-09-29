#!/usr/bin/env python3
from __future__ import annotations
import json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path


def backup(path, tag):
    path = Path(path)
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dst = path.with_name(path.name + f'.bak-{tag}-{stamp}')
    shutil.copy2(path, dst)
    return dst


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: install-syntal-ai-v394-contract.py /opt/syntal-ai/core')
    root = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1] / 'config' / 'njs-v3.9.4-contract.json'
    contract = json.loads(source.read_text())
    if contract.get('njs_version') != '3.9.4' or contract.get('tool_count') != 127 or len(contract.get('tools') or []) != 127:
        raise SystemExit('Bundled NJS v3.9.4 contract is invalid')

    config = root / 'config'
    module = root / 'app' / 'njs_v37.py'
    tools_module = root / 'app' / 'tools.py'
    if not module.exists():
        raise SystemExit(f'Syntal AI source not found under {root}')
    config.mkdir(exist_ok=True)

    targets = [
        config / 'njs-v3.7-contract.json',
        config / 'njs-v3.9-contract.json',
        config / 'njs-v3.9.1-contract.json',
        config / 'njs-v3.9.2-contract.json',
        config / 'njs-v3.9.3-contract.json',
        config / 'njs-v3.9.4-contract.json',
    ]
    for target in targets:
        backup(target, 'before-njs-v394')
        shutil.copy2(source, target)

    text = module.read_text()
    marker = '# NJS v3.9.4 contract compatibility installed by NJS v3.9.4'
    if marker not in text:
        backup(module, 'before-njs-v394')
        text += f'''\n\n{marker}\nLABEL_OVERRIDES.update({{\n    "njs.newsletters.editions.update": "Edit newsletter copy or visual design",\n}})\n'''
        module.write_text(text)

    if tools_module.exists():
        text = tools_module.read_text()
        changed = text
        for previous in ('njs-v3.9.3', 'njs-v3.9.2', 'njs-v3.9.1', 'njs-v3.9', 'njs-v3.8', 'njs-v3.7'):
            changed = changed.replace(f'"{previous}" if app == "njs" else', '"njs-v3.9.4" if app == "njs" else')
        if changed != text:
            backup(tools_module, 'before-njs-v394')
            tools_module.write_text(changed)

    print(f'Installed NJS v3.9.4 contract with {contract["tool_count"]} tools.')
    print(f'Versioned contract: {config / "njs-v3.9.4-contract.json"}')
    print('Restart Syntal AI web so the cached contract reloads.')


if __name__ == '__main__':
    main()
