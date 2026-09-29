# Upgrade to NJS v3.9.2

v3.9.2 is an in-place upgrade from v3.9.1. Preserve the existing NJS `.env` and persistent volumes.

## 1. Install NJS v3.9.2

Replace the application source while preserving `/opt/newsjacking-core/.env`, then rebuild/recreate NJS web, worker and beat using the existing deployment pattern.

Do not run `docker compose down -v`.

## 2. Update the BlackBook newsletter bridge

The existing v3.9.1 Engagement Intelligence bridge remains installed. Upgrade only the newsletter marketing bridge to v2:

```bash
python3 deploy/install-blackbook-newsletter-bridge-v2.py /opt/blackbook/core/app.py
```

Then rebuild/recreate the BlackBook web service. The worker does not require new task code for Smart Audience resolution, but recreating both web and worker together is safe if that is your normal deployment procedure.

The installer backs up `app.py` before replacing the existing Newsletter Bridge v1/v2 block.

## 3. Keep Engagement Intelligence v3 installed

If the BlackBook Engagement Intelligence v3 bridge is not already installed, run:

```bash
python3 deploy/install-blackbook-engagement-intelligence-v3.py /opt/blackbook/core
```

This provides the canonical-interest catalog and engagement-bucket data used by Smart Audience.

## 4. Update Syntal AI contract

```bash
python3 deploy/install-syntal-ai-v392-contract.py /opt/syntal-ai/core
```

Restart Syntal AI web after installing the contract.

The tool count remains 127.

## 5. Verification

Open `Newsletters → Audience` and confirm:

- engagement bucket cards show counts;
- canonical interests are searchable;
- ANY/ALL matching can be changed;
- selecting criteria updates the live preview;
- saving and reloading retains the criteria;
- Mailchimp draft creation resolves the saved audience successfully.
