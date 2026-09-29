# Upgrade to NJS v3.9.0

v3.9.0 is an in-place upgrade from v3.8.0 and uses the same `syntal-njs` Compose identity and persistent volumes.

Do not run `docker compose down -v`.

## Required components

The Engagement Intelligence workspace depends on BlackBook v13.11.1 plus the bundled engagement bridge.

After installing NJS v3.9.0, run:

```bash
python3 deploy/install-blackbook-engagement-intelligence-v2.py /opt/blackbook/core
```

Then rebuild BlackBook `web`, `worker`, and `beat` so the new API routes and Celery task are loaded.

## Syntal AI

Install the matching 127-tool contract:

```bash
python3 deploy/install-syntal-ai-v39-contract.py /opt/syntal-ai/core
```

Restart the Syntal AI web service and refresh the NJS connection.

## First use

Open `/newsletters/engagement`, click **Refresh Mailchimp queue**, then use **Max select 1000** in Queue and submit **Bulk import engagement & analyze**.
