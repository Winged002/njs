"""Client for the restricted host-side NJS custom-domain provisioner."""
from __future__ import annotations

import json
import socket

from config import Config


class DomainProvisioningError(RuntimeError):
    pass


def _request(operation: str, domain: str) -> dict:
    if not Config.DOMAIN_AUTO_PROVISION:
        raise DomainProvisioningError("Automatic domain provisioning is disabled")
    token = (Config.DOMAIN_PROVISIONER_TOKEN or "").strip()
    if not token:
        raise DomainProvisioningError("DOMAIN_PROVISIONER_TOKEN is not configured")

    payload = {
        "token": token,
        "operation": operation,
        "domain": domain,
        "expected_ip": Config.PUBLISHING_IP,
        "app_port": Config.DOMAIN_PROVISIONER_APP_PORT,
    }
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > 16384:
        raise DomainProvisioningError("Provisioning request is too large")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(Config.DOMAIN_PROVISIONER_TIMEOUT_SECONDS)
    try:
        sock.connect(Config.DOMAIN_PROVISIONER_SOCKET)
        sock.sendall(raw)
        chunks = []
        total = 0
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 65536:
                raise DomainProvisioningError("Provisioner returned an oversized response")
            if b"\n" in chunk:
                break
    except (OSError, socket.timeout) as exc:
        raise DomainProvisioningError(f"Domain provisioner is unavailable: {exc}") from exc
    finally:
        sock.close()

    try:
        response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    except Exception as exc:
        raise DomainProvisioningError("Domain provisioner returned an invalid response") from exc

    if not response.get("ok"):
        raise DomainProvisioningError(response.get("error") or "Domain provisioning failed")
    return response


def provision_domain(domain: str) -> dict:
    return _request("provision", domain)


def deprovision_domain(domain: str) -> dict:
    return _request("deprovision", domain)


def provisioner_status(domain: str) -> dict:
    return _request("status", domain)
