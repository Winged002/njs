#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SERVICE_ENV="/etc/njs-domain-provisioner.env"
HELPER_DST="/usr/local/lib/njs/njs-domain-provisioner.py"
SERVICE_DST="/etc/systemd/system/njs-domain-provisioner.service"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 2
fi
if ! command -v nginx >/dev/null; then
  echo "nginx is required." >&2; exit 3
fi
if ! command -v certbot >/dev/null; then
  echo "certbot is required. Install certbot and python3-certbot-nginx first." >&2; exit 3
fi

install -d -m 0755 /usr/local/lib/njs
install -m 0755 "${ROOT_DIR}/deploy/njs-domain-provisioner.py" "$HELPER_DST"
install -m 0644 "${ROOT_DIR}/deploy/njs-domain-provisioner.service" "$SERVICE_DST"

TOKEN=""
if [[ -f "$SERVICE_ENV" ]]; then
  TOKEN="$(grep -E '^NJS_PROVISIONER_TOKEN=' "$SERVICE_ENV" | tail -1 | cut -d= -f2- || true)"
fi
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
fi

PUBLISHING_IP="${PUBLISHING_IP:-95.179.253.170}"
APP_PORT="${HOST_PORT:-8010}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
if [[ -f "$ENV_FILE" ]]; then
  get_env(){ grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
  PUBLISHING_IP="$(get_env PUBLISHING_IP)"; PUBLISHING_IP="${PUBLISHING_IP:-95.179.253.170}"
  APP_PORT="$(get_env HOST_PORT)"; APP_PORT="${APP_PORT:-8010}"
  [[ -n "$(get_env CERTBOT_EMAIL)" ]] && CERTBOT_EMAIL="$(get_env CERTBOT_EMAIL)"
fi

cat > "$SERVICE_ENV" <<EOF
NJS_PROVISIONER_TOKEN=${TOKEN}
NJS_PUBLISHING_IP=${PUBLISHING_IP}
NJS_APP_PORT=${APP_PORT}
NJS_CERTBOT_EMAIL=${CERTBOT_EMAIL}
NJS_PROVISIONER_SOCKET=/run/njs-domain-provisioner/provision.sock
NJS_SOCKET_GID=10001
EOF
chmod 0600 "$SERVICE_ENV"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "${ROOT_DIR}/.env.example" "$ENV_FILE"
  echo "Created ${ENV_FILE}; fill required application secrets before starting NJS." >&2
fi
python3 - "$ENV_FILE" "$TOKEN" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1]); token = sys.argv[2]
text = path.read_text()
updates = {
    "DOMAIN_AUTO_PROVISION": "true",
    "DOMAIN_PROVISIONER_SOCKET": "/run/njs-domain-provisioner/provision.sock",
    "DOMAIN_PROVISIONER_TOKEN": token,
    "DOMAIN_PROVISIONER_TIMEOUT_SECONDS": "210",
}
lines = text.splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
    if key in updates:
        out.append(f"{key}={updates[key]}"); seen.add(key)
    else:
        out.append(line)
if seen != set(updates):
    out += ["", "# v2.9 automatic custom-domain provisioning"]
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
path.write_text("\n".join(out) + "\n")
PY

systemctl daemon-reload
systemctl enable --now njs-domain-provisioner.service
systemctl --no-pager --full status njs-domain-provisioner.service | sed -n '1,12p'

echo
echo "NJS automatic domain provisioner installed."
echo "Socket: /run/njs-domain-provisioner/provision.sock"
echo "NJS .env updated with the matching token."
echo "Rebuild/restart the NJS web container so it receives the socket and token."
