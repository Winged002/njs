# NJS v3.9.0 — Engagement Intelligence

v3.9.0 adds a queue-driven Mailchimp engagement-analysis workspace to NJS while keeping BlackBook as the CRM, Mailchimp integration owner and behavioral-analysis source of truth.

## Engagement Intelligence

A new **Newsletters → Engagement** workspace provides six operational views:

- **Queue** — Mailchimp audience members that have not completed the v3.9 engagement-intelligence workflow.
- **Inactive** — analyzed people with zero meaningful Mailchimp engagement.
- **Low** — engagement score 1–39.
- **Medium** — engagement score 40–69.
- **High** — engagement score 70–100.
- **All** — all Mailchimp audience members with fast interest filtering.

The Queue view exposes a **Max select 1000** action. The operator can select up to 1,000 people without clicking each checkbox and submit one background **Bulk import engagement & analyze** batch.

## Bulk pipeline

The companion BlackBook bridge performs the expensive work in BlackBook/Celery:

1. refresh Mailchimp campaign reports;
2. import available Mailchimp campaign activity;
3. evaluate the selected people only;
4. place people with zero opens/clicks/conversation signals in `inactive`;
5. run BlackBook's existing evidence-disciplined behavioral AI analysis for people with meaningful engagement;
6. classify engagement using the AI `engagement_level` score;
7. reconcile inferred interests against a reusable organization-scoped canonical interest catalog;
8. create a new canonical interest only when no sufficiently similar existing interest is found;
9. write normalized person↔interest mappings so later filtering does not require re-running AI.

## Reusable interest catalog

If Person A is analyzed with `Interest 1`, `Interest 2`, and `Interest 3`, those canonical interests become available immediately. When Person B is analyzed, semantically equivalent inferred interest names reuse those existing catalog records. A genuinely new `Interest 4` creates a new canonical record.

The catalog stores aliases, canonical keys, per-person scores/confidence and engagement bucket. NJS can then filter the audience by interest without reparsing AI output.

## BlackBook companion bridge

The release includes:

- `deploy/blackbook-engagement-intelligence-v2.inc.py`
- `deploy/install-blackbook-engagement-intelligence-v2.py`

Install it into the current BlackBook v13.11.1 source. It preserves the existing prospect intake, NJS Newsletter Marketing Bridge, Mailchimp configuration and BlackBook AI control plane while adding:

- `GET /api/v1/marketing/engagement/overview`
- `POST /api/v1/marketing/engagement/queue/refresh`
- `GET /api/v1/marketing/engagement/people`
- `POST /api/v1/marketing/engagement/bulk-analyze`
- `GET /api/v1/marketing/engagement/interests`
- `GET /api/v1/marketing/engagement/batches`
- `GET /api/v1/marketing/engagement/batches/{id}`

## Syntal AI

The NJS control plane expands from 120 to **127 tools**. New tools expose the same Engagement Intelligence workflow to Syntal AI:

- `njs.engagement.overview`
- `njs.engagement.people.list`
- `njs.engagement.queue.refresh`
- `njs.engagement.bulk_analyze`
- `njs.engagement.interests.list`
- `njs.engagement.batches.list`
- `njs.engagement.batches.read`

Bulk analysis is marked `external` and the bundled Syntal AI contract installer adds it to mandatory approval because it can launch up to 1,000 AI analyses.

## Existing product retained

v3.8 Landing Page Studio, v3.7.1 Newsletter UI merge, SSO delegated authorization, BlackBook newsletter delivery, domain/site tooling and all previous NJS functionality remain intact.
