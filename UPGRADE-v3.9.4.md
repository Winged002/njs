# Upgrade to NJS v3.9.4

v3.9.4 is an in-place upgrade from v3.9.3. Preserve the current NJS `.env` and all persistent volumes.

## 1. Replace NJS source

Install the v3.9.4 source over `/opt/newsjacking-core` while preserving `.env`.

Do not run `docker compose down -v`.

## 2. Install BlackBook Engagement Intelligence bridge v4

This step is required. It replaces the existing v2/v3 engagement bridge in BlackBook and preserves the rest of BlackBook `app.py`.

```bash
python3 deploy/install-blackbook-engagement-intelligence-v4.py /opt/blackbook/core
python3 -m py_compile /opt/blackbook/core/app.py
```

The installer also ensures the BlackBook `.env` contains:

```bash
NJS_ENGAGEMENT_MAX_SELECT=2500
```

Rebuild both BlackBook web and worker so the new route/state logic and Celery tasks load.

## 3. Update Syntal AI contract

```bash
python3 deploy/install-syntal-ai-v394-contract.py /opt/syntal-ai/core
```

Restart/rebuild Syntal AI web. Tool count remains 127.

## 4. Rebuild NJS

Rebuild NJS web, worker and beat from the v3.9.4 source.

## 5. Verify

```bash
python3 deploy/check-v3.9.4.py
```

Then verify in Engagement Intelligence:

- Max select shows 2500;
- Failed appears as a separate tab and metric;
- a per-contact 403 moves that contact to Failed;
- Queue count decreases for that contact;
- other contacts in the batch continue;
- the batch reaches 100% and can report Partial with a failed count;
- selecting contacts in Failed and retrying them starts a new analysis attempt.
