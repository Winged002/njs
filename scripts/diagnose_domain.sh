#!/usr/bin/env bash
set -u
DOMAIN="${1:-}"
PORT="${HOST_PORT:-8010}"
if [[ -z "$DOMAIN" ]]; then
  echo "Usage: $0 <domain>" >&2; exit 2
fi
printf '== DNS ==\n'
getent ahostsv4 "$DOMAIN" 2>&1 | awk '{print $1}' | sort -u || true
printf '\n== Nginx vhost ==\n'
nginx -T 2>/dev/null | grep -n -A18 -B3 "server_name ${DOMAIN}" || true
printf '\n== NJS upstream with Host header ==\n'
curl -sS -o /dev/null -D - -H "Host: ${DOMAIN}" "http://127.0.0.1:${PORT}/" | sed -n '1,12p' || true
printf '\n== Public HTTPS ==\n'
curl -k -sS -o /dev/null -D - "https://${DOMAIN}/" | sed -n '1,12p' || true
printf '\n== Provisioner ==\n'
systemctl --no-pager --full status njs-domain-provisioner.service 2>&1 | sed -n '1,14p' || true
