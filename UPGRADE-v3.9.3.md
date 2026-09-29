# Upgrade to NJS v3.9.3

v3.9.3 is an in-place upgrade from v3.9.2. Preserve the existing NJS `.env` and persistent volumes.

## 1. Replace the NJS source

Install the v3.9.3 source over `/opt/newsjacking-core` while preserving `.env`.

Do **not** run `docker compose down -v`.

No BlackBook bridge change is required beyond the v3.9.2 Newsletter Bridge v2 and v3.9.1 Engagement Intelligence v3 already used by Smart Audience.

## 2. Optional design-model environment values

The defaults reuse the newsletter text model, so no new environment variable is required. They can be overridden with:

```bash
NEWSLETTER_DESIGN_MODEL=deepseek-v4-flash
NEWSLETTER_DESIGN_TEMPERATURE=0.45
```

## 3. Update the Syntal AI contract

```bash
python3 deploy/install-syntal-ai-v393-contract.py /opt/syntal-ai/core
```

Restart Syntal AI web after installing the contract. Tool count remains 127.

## 4. Rebuild NJS web and worker

The new visual design task runs in the NJS worker, so both web and worker must run the v3.9.3 image. Recreate beat as part of the normal NJS deployment for version consistency.

## 5. Verify

```bash
python3 deploy/check-v3.9.3.py
```

Then generate/open a newsletter edition and confirm:

- the **Visual design** section appears;
- a prompt queues a design job;
- the page refreshes when the design finishes;
- desktop/mobile preview uses the new HTML;
- a previous visual revision can be restored;
- **Use this direction for future editions** persists the design brief on the newsletter plan;
- HTML export and Mailchimp draft creation use the currently selected revision.
