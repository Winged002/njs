# Upgrade to NJS v3.9.1

v3.9.1 is an in-place upgrade from v3.9.0 and uses the same NJS/BlackBook persistent data.

## NJS

Preserve `/opt/newsjacking-core/.env`, install the release source, then rebuild NJS web/worker/beat.

## BlackBook companion bridge

Install the v3 bridge:

```bash
python3 deploy/install-blackbook-engagement-intelligence-v3.py /opt/blackbook/core
```

The installer:

- replaces the v2 Engagement Intelligence bridge if present;
- preserves/restores NJS Marketing Bridge CSRF exemptions;
- installs the parallel person-chain tasks;
- changes hard-coded BlackBook worker concurrency 2 to configurable default 6.

Recommended `.env` values:

```dotenv
BLACKBOOK_WORKER_CONCURRENCY=6
NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT=20
NJS_ENGAGEMENT_AI_CAMPAIGN_LIMIT=20
NJS_ENGAGEMENT_CATALOG_AI_LIMIT=250
```

Rebuild/recreate BlackBook web and worker after installation.

## Syntal AI

Install the unchanged 127-tool contract with the new NJS version:

```bash
python3 deploy/install-syntal-ai-v391-contract.py /opt/syntal-ai/core
```

Restart Syntal AI web and refresh connections.

## Existing v3.9.0 jobs

Tasks already running under the old v3.9.0 worker continue using the old code until the worker is recreated. Recreate the BlackBook worker before submitting new batches. Old duplicate/long-running batch task IDs may be revoked if they are no longer needed.

Do not run `docker compose down -v`.
