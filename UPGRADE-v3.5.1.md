# Upgrade NJS v3.5.0 -> v3.5.1

v3.5.1 is a non-destructive SSO/navigation update. It adds the SSO-backed organization dropdown and encrypted server-side SSO token retention. No MongoDB migration and no volume reset are required.

## Configuration

Existing `.env` files remain compatible. These optional values have production defaults:

```env
SSO_ORGANIZATIONS_ENDPOINT=https://sso.syntal.pro/v1/organizations
SSO_ORGANIZATIONS_CACHE_SECONDS=120
```

Keep the same `SECRET_KEY` when upgrading. It is now also used to derive the encryption key for retained SSO access/refresh tokens. Rotating `SECRET_KEY` intentionally makes previously retained SSO credentials unreadable; users can simply reauthenticate through Syntal to replace them.

The default OIDC scopes include `offline_access` so SSO can issue a refresh token when permitted:

```env
SSO_SCOPES="openid profile email organization permissions offline_access"
```

## Upgrade

Preserve the current `.env`, copy the v3.5.1 release over `/opt/newsjacking-core`, restore `.env`, then rebuild and recreate the application containers:

```bash
cd /opt/newsjacking-core
docker compose -f compose.yml build --pull web worker beat
docker compose -f compose.yml up -d --remove-orphans
```

Do not run `docker compose down -v`.

## Verify

```bash
cd /opt/newsjacking-core
docker compose -f compose.yml ps
curl -fsS http://127.0.0.1:8010/health && echo
docker compose -f compose.yml logs --tail=100 web
```

Expected health version: `3.5.1`.

After login, open the organization control in either navigation location. Existing browser sessions from before v3.5.1 may initially show only the current organization; use **Choose in Syntal** once to reauthenticate, then reopen the dropdown.
