#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    raise SystemExit("FAIL: " + message)


def ok(message):
    print("OK:", message)


# Every Python file must parse.
py_files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "deploy").glob("*.py"))
for path in py_files:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"Python syntax: {path.name}: {exc}")
ok(f"Python syntax ({len(py_files)} files)")

# Version markers.
config = (ROOT / "config.py").read_text()
compose_text = (ROOT / "compose.yml").read_text()
readme = (ROOT / "README.md").read_text()
if 'APP_VERSION = os.getenv("APP_VERSION", "3.7.0")' not in config:
    fail("config default version is not 3.7.0")
if "APP_VERSION: 3.7.0" not in compose_text:
    fail("compose APP_VERSION is not 3.7.0")
if not readme.startswith("# Newsjacking Core v3.7.0"):
    fail("README does not identify v3.7.0")
ok("v3.7.0 markers")

# Compose identity and dedicated runtime names.
compose = yaml.safe_load(compose_text)
if compose.get("name") != "syntal-njs-v370":
    fail("Compose project name is not syntal-njs-v370")
expected = {
    "web": "syntal-njs-v370-web",
    "worker": "syntal-njs-v370-worker",
    "beat": "syntal-njs-v370-beat",
    "mongo": "syntal-njs-v370-mongo",
    "redis": "syntal-njs-v370-redis",
}
for service, name in expected.items():
    actual = (compose.get("services", {}).get(service, {}) or {}).get("container_name")
    if actual != name:
        fail(f"{service} container_name is {actual!r}, expected {name!r}")
ok("isolated Compose/container identity")

# Extract the manifest tool declarations without importing application deps.
ai_path = ROOT / "ai_control.py"
tree = ast.parse(ai_path.read_text(), filename=str(ai_path))
tool_calls = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets):
        tool_calls = list(node.value.elts)
        break
if tool_calls is None:
    fail("TOOLS declaration missing")
if len(tool_calls) != 56:
    fail(f"expected 56 tools, found {len(tool_calls)}")

names = []
manifest_routes = []
for call in tool_calls:
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "_tool":
        fail("TOOLS contains a non-_tool expression")
    name, _description, risk, method, path = [ast.literal_eval(x) for x in call.args[:5]]
    if not re.fullmatch(r"njs\.[a-z0-9_.-]+", name):
        fail(f"invalid tool name {name}")
    if risk not in {"read", "write", "external", "destructive", "admin"}:
        fail(f"invalid risk {risk} for {name}")
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        fail(f"invalid method {method} for {name}")
    if not path.startswith("/api/ai/v1/"):
        fail(f"tool path is outside AI namespace: {name}: {path}")
    names.append(name)
    manifest_routes.append((method, path))
if len(set(names)) != len(names):
    fail("duplicate tool names")
ok("56 unique Syntal AI tool declarations")

# Every manifest method/path must have a matching Flask Blueprint route.
routes = set()
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for deco in node.decorator_list:
        if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
            continue
        if not isinstance(deco.func.value, ast.Name) or deco.func.value.id != "bp":
            continue
        method = deco.func.attr.upper()
        if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"} or not deco.args:
            continue
        path = ast.literal_eval(deco.args[0])
        normalized = re.sub(r"<([^>]+)>", r"{\1}", path)
        routes.add((method, normalized))
missing = [item for item in manifest_routes if item not in routes]
if missing:
    fail("manifest routes missing handlers: " + repr(missing[:10]))
ok("all manifest tools map to implemented Blueprint routes")

source = ai_path.read_text()
for required in [
    'request.headers.get("X-Syntal-AI")',
    'request.headers.get("X-Syntal-Actor-User")',
    'request.headers.get("X-Syntal-Actor-Org")',
    'requests.get(cfg["userinfo_endpoint"]',
    'Config.SSO_ADMIN_PERMISSION',
    'Config.SSO_REQUIRED_PERMISSION',
]:
    if required not in source:
        fail(f"authorization invariant missing: {required}")
ok("SSO/user/org authorization invariants")

app_source = (ROOT / "app.py").read_text()
if "app.register_blueprint(syntal_ai_control_bp)" not in app_source or "csrf.exempt(syntal_ai_control_bp)" not in app_source:
    fail("AI Blueprint is not registered/CSRF-exempted")
ok("AI Blueprint registration")

print("PASS: NJS v3.7.0 offline release checks")
