#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
EXPECTED_IP="${PUBLISHING_IP:-95.179.253.170}"
APP_PORT="${HOST_PORT:-8010}"

if [[ -z "$DOMAIN" ]]; then
  echo "Usage: sudo $0 <verified-domain>" >&2
  exit 2
fi
DOMAIN="${DOMAIN,,}"
DOMAIN="${DOMAIN%.}"
if ! [[ "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "Invalid domain: $DOMAIN" >&2
  exit 2
fi

if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
  echo "nginx and certbot are required on the host." >&2
  exit 3
fi

RESOLVED="$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u || true)"
if ! grep -qx "$EXPECTED_IP" <<<"$RESOLVED"; then
  echo "DNS check failed. $DOMAIN must resolve to $EXPECTED_IP before TLS provisioning." >&2
  echo "Current IPv4 results: ${RESOLVED:-none}" >&2
  exit 4
fi

SITE="/etc/nginx/sites-available/njs-publish-${DOMAIN}"
LINK="/etc/nginx/sites-enabled/njs-publish-${DOMAIN}"
cat > "$SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 30s;
        proxy_send_timeout 180s;
        proxy_read_timeout 180s;
    }
}
NGINX
ln -sfn "$SITE" "$LINK"
nginx -t
systemctl reload nginx
certbot --nginx -d "$DOMAIN" --redirect
nginx -t
systemctl reload nginx

echo "HTTPS publishing proxy ready: https://${DOMAIN}/"
