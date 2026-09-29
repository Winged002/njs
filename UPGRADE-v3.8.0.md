# Upgrade to NJS v3.8.0

v3.8.0 is an in-place upgrade from v3.7.1.

## Preserve

- `/opt/newsjacking-core/.env`
- current Mongo volume
- current Redis volume
- generated-media volume
- Collection-file volume

The Compose project remains `syntal-njs`; no data-volume migration is required when upgrading from v3.7.1 with the same `.env` volume configuration.

Do not run `docker compose down -v`.

## Required SSO setting

Production should use:

```dotenv
SSO_AI_DELEGATION_ENDPOINT=https://sso.syntal.pro/v1/ai/delegation-check
```

The SSO endpoint must evaluate the Syntal AI bearer subject in the active organization against target NJS permissions.

## Syntal AI contract

After NJS is healthy, install the 120-tool contract into Syntal AI:

```bash
cd /opt/newsjacking-core
python3 deploy/install-syntal-ai-v38-contract.py /opt/syntal-ai/core
```

Then rebuild/restart Syntal AI web and use Control Center / Tools → Refresh connections.

The NJS manifest should report `app_version: 3.8.0` and `tool_count: 120`.
