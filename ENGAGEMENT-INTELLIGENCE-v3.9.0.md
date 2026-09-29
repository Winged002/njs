# Engagement Intelligence architecture

NJS is the operator and AI-control surface. BlackBook remains the authoritative CRM and owns Mailchimp credentials, Mailchimp activity, people records and behavioral AI analysis.

## State model

Each BlackBook person receives `marketing.engagement_intelligence` after a successful v3.9 analysis:

- `bucket`: inactive / low / medium / high;
- `score`: 0–100;
- `status`: complete / error;
- `event_counts` and meaningful-event count;
- canonical interest IDs/keys/names;
- import and analysis timestamps;
- originating batch.

People without a completed bucket remain in the NJS Queue. Errors return to Queue with the error message visible.

## Interest normalization

`marketing_interest_catalog` contains reusable canonical interests. `marketing_person_interests` maps people to canonical interests with score, confidence, rationale and evidence.

Canonicalization uses normalized keys, known aliases and conservative string/token similarity. New catalog records are created only when no existing interest passes the reuse threshold. This avoids duplicate labels while allowing the vocabulary to expand over time.

## Batch safety

A single NJS request accepts at most 1,000 unique people. The request creates a BlackBook background batch and returns immediately. Expensive Mailchimp import and AI analysis run in BlackBook's Celery worker.

NJS never receives Mailchimp API credentials.

## Optional BlackBook environment controls

Defaults are suitable for the requested workflow, but the bridge accepts:

```dotenv
NJS_ENGAGEMENT_MAX_SELECT=1000
NJS_ENGAGEMENT_LOW_MAX=39
NJS_ENGAGEMENT_MEDIUM_MAX=69
```

`NJS_ENGAGEMENT_MAX_SELECT` is always capped at 1,000 even if a larger value is supplied.
