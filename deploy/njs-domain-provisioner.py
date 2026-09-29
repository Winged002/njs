#!/usr/bin/env python3
"""Restricted host daemon for NJS custom-domain Nginx/TLS provisioning.

Listens only on a Unix socket. It accepts three whitelisted operations and never
accepts shell fragments or arbitrary configuration text from the web app.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import tempfile
from pathlib import Path

SOCKET_PATH = os.getenv("NJS_PROVISIONER_SOCKET", "/run/njs-domain-provisioner/provision.sock")
TOKEN = os.getenv("NJS_PROVISIONER_TOKEN", "")
EXPECTED_IP = os.getenv("NJS_PUBLISHING_IP", "95.179.253.170")
APP_PORT = int(os.getenv("NJS_APP_PORT", "8010"))
CERTBOT_EMAIL = os.getenv("NJS_CERTBOT_EMAIL", "").strip()
SOCKET_GID = int(os.getenv("NJS_SOCKET_GID", "10001"))
SITE_PREFIX = "/etc/nginx/sites-available/njs-publish-"
ENABLED_PREFIX = "/etc/nginx/sites-enabled/njs-publish-"
DOMAIN_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def run(cmd, *, timeout=120):
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "command failed").strip()
        raise RuntimeError(f"{' '.join(cmd[:2])} failed: {message[-1500:]}")
    return (proc.stdout or "").strip()


def normalize_domain(value):
    domain = str(value or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(domain):
        raise ValueError("Invalid domain name")
    return domain


def resolve_ipv4(domain):
    try:
        infos = socket.getaddrinfo(domain, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({item[4][0] for item in infos if item and item[4]})


def nginx_paths(domain):
    return Path(SITE_PREFIX + domain), Path(ENABLED_PREFIX + domain)


def nginx_config(domain, port):
    return f'''server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    client_max_body_size 20M;

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 30s;
        proxy_send_timeout 180s;
        proxy_read_timeout 180s;
    }}
}}
'''


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def ensure_upstream(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return
    except OSError as exc:
        raise RuntimeError(f"NJS upstream 127.0.0.1:{port} is not reachable: {exc}") from exc


def ensure_tls(domain):
    cmd = ["certbot", "--nginx", "--non-interactive", "--agree-tos", "--redirect", "-d", domain]
    if CERTBOT_EMAIL:
        cmd.extend(["--email", CERTBOT_EMAIL])
    else:
        cmd.append("--register-unsafely-without-email")
    run(cmd, timeout=180)

    # Validate SNI/certificate locally; the page may legitimately return 404 until a route is mapped.
    context = ssl.create_default_context()
    with socket.create_connection(("127.0.0.1", 443), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=domain) as tls:
            cert = tls.getpeercert()
            if not cert:
                raise RuntimeError("TLS certificate validation returned no peer certificate")


def provision(domain, expected_ip, port):
    if expected_ip != EXPECTED_IP:
        raise ValueError("Publishing IP does not match host policy")
    if int(port) != APP_PORT:
        raise ValueError("Application port does not match host policy")
    if not shutil.which("nginx") or not shutil.which("certbot"):
        raise RuntimeError("nginx and certbot must be installed on the host")
    resolved = resolve_ipv4(domain)
    if EXPECTED_IP not in resolved:
        raise RuntimeError(f"DNS for {domain} does not resolve to {EXPECTED_IP}; current IPv4: {', '.join(resolved) or 'none'}")
    ensure_upstream(APP_PORT)

    site, link = nginx_paths(domain)
    backup = site.read_text(encoding="utf-8") if site.exists() else None
    atomic_write(site, nginx_config(domain, APP_PORT))
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(site)
    try:
        run(["nginx", "-t"], timeout=20)
        run(["systemctl", "reload", "nginx"], timeout=30)
        ensure_tls(domain)
        run(["nginx", "-t"], timeout=20)
        run(["systemctl", "reload", "nginx"], timeout=30)
    except Exception:
        # Roll back only the site we manage. Certbot changes are left intact if already created.
        if backup is None:
            try:
                link.unlink(missing_ok=True)
                site.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            try:
                atomic_write(site, backup)
            except Exception:
                pass
        try:
            run(["nginx", "-t"], timeout=20)
            run(["systemctl", "reload", "nginx"], timeout=30)
        except Exception:
            pass
        raise
    return {"active": True, "tls": True, "resolved_ips": resolved}


def deprovision(domain):
    site, link = nginx_paths(domain)
    changed = False
    if link.exists() or link.is_symlink():
        link.unlink(missing_ok=True)
        changed = True
    if site.exists():
        site.unlink()
        changed = True
    if changed:
        run(["nginx", "-t"], timeout=20)
        run(["systemctl", "reload", "nginx"], timeout=30)
    return {"active": False, "removed": changed, "certificate_retained": True}


def status(domain):
    site, link = nginx_paths(domain)
    return {"active": site.exists() and link.exists(), "site": str(site), "enabled": link.exists()}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(16385)
        response = {"ok": False}
        try:
            if len(line) > 16384:
                raise ValueError("Request too large")
            data = json.loads(line.decode("utf-8"))
            if not TOKEN or not hmac.compare_digest(str(data.get("token") or ""), TOKEN):
                raise PermissionError("Unauthorized")
            domain = normalize_domain(data.get("domain"))
            op = data.get("operation")
            if op == "provision":
                result = provision(domain, str(data.get("expected_ip") or ""), int(data.get("app_port") or 0))
            elif op == "deprovision":
                result = deprovision(domain)
            elif op == "status":
                result = status(domain)
            else:
                raise ValueError("Unsupported operation")
            response = {"ok": True, "domain": domain, **result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    if not TOKEN:
        raise SystemExit("NJS_PROVISIONER_TOKEN is required")
    sock = Path(SOCKET_PATH)
    sock.parent.mkdir(parents=True, exist_ok=True)
    if sock.exists() or sock.is_socket():
        sock.unlink()
    server = UnixServer(SOCKET_PATH, Handler)
    os.chmod(SOCKET_PATH, 0o660)
    os.chown(SOCKET_PATH, 0, SOCKET_GID)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            sock.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
