# Newsjacking Core v3.9.5.3 — Monolith Media Contract

v3.9.5.3 restores the legacy monolith article image contract while keeping the newer Collection/OpenAI media pipeline. Generated articles persist a real cover image under `metadata.image_info.url`, mirror it to `metadata.hero_image`, and inject verified image URLs directly into `article.content`.

# Newsjacking Core v3.9.4 — Failed Engagement Queue

NJS v3.9.4 builds directly on v3.9.3 Prompt Newsletter Design Studio. It keeps the prompt-driven newsletter visual-design workflow and upgrades Engagement Intelligence so per-contact Mailchimp/API failures become a separate terminal **Failed** state instead of remaining in Queue.

## v3.9.4 highlights

- Engagement Intelligence now has a dedicated **Failed** tab and metric.
- A contact that fails import or analysis is removed from Queue and persisted as `bucket=failed`.
- Mailchimp 403 / Access Denied / Forbidden errors are normalized to `mailchimp_access_denied`.
- Failure message, stage, timestamp, batch ID and failure count are retained for review.
- Other contacts in the same batch continue normally.
- Partial batches still complete to 100% and report a failed count.
- Failed contacts can be deliberately selected and retried from the Failed tab.
- Bulk engagement analysis now supports up to **2,500 contacts** per batch end-to-end.
- The BlackBook bridge v4 installer ensures `NJS_ENGAGEMENT_MAX_SELECT=2500` in the BlackBook `.env`.

## Newsletter visual design retained from v3.9.3

Generated newsletter editions retain the prompt-driven **Visual design** workspace. Operators can redesign an email using plain language while preserving its stories, links and factual content. The workflow includes desktop/mobile preview, non-destructive design revisions, restore, advanced HTML source editing, and reusable visual direction for future editions.

## Engagement-state model

The Engagement Intelligence workspace now separates processing state from successful engagement classification:

- `queue` — waiting to be analyzed;
- `failed` — latest per-contact attempt ended in an exception;
- `inactive` — successfully processed with no meaningful engagement;
- `low`, `medium`, `high` — successfully AI-classified engagement.

`failed` is intentionally not a Smart Audience engagement segment. Newsletter audience targeting continues to use the four successful engagement buckets: Inactive, Low, Medium and High.

## Syntal AI

Tool count remains **127**.

- `njs.engagement.people.list` supports `bucket=failed` and returns failure metadata.
- `njs.engagement.bulk_analyze` accepts up to 2,500 person IDs.
- Existing newsletter visual-design tools from v3.9.3 are unchanged.

Contracts:

- `config/njs-v3.9.4-contract.json`
- `NJS-AI-MANIFEST-v3.9.4.json`

## Upgrade requirements

v3.9.4 requires the bundled BlackBook Engagement Intelligence bridge v4 because the failed-state persistence and 2,500-contact enforcement live in BlackBook's worker/API path.

Use:

```bash
python3 deploy/install-blackbook-engagement-intelligence-v4.py /opt/blackbook/core
python3 deploy/install-syntal-ai-v394-contract.py /opt/syntal-ai/core
python3 deploy/check-v3.9.4.py
```

Do not remove persistent Docker volumes during the upgrade.

See `RELEASE-NOTES-v3.9.4.md`, `ENGAGEMENT-INTELLIGENCE-v3.9.4.md`, `UPGRADE-v3.9.4.md`, and the retained `NEWSLETTER-DESIGN-v3.9.3.md`.
