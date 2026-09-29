#!/usr/bin/env python3
"""Activate existing NJS domains whose DNS already points at the publishing IP."""
from app import activate_domain_after_dns
from db import domain_mappings


def main():
    rows = list(domain_mappings.find({"status": {"$in": ["pending", "verified", "provisioning_failed", "active"]}}).sort("domain", 1))
    if not rows:
        print("No custom domains found.")
        return 0
    failures = 0
    for domain in rows:
        ok, message, updated = activate_domain_after_dns(domain)
        print(f"[{updated.get('status')}] {domain.get('domain')}: {message}")
        if updated.get("status") == "provisioning_failed":
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
