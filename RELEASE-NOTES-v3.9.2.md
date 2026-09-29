# NJS v3.9.2 — Smart Audience Builder

v3.9.2 makes Engagement Intelligence directly usable for newsletter targeting.

## Smart Audience

Newsletter plans now persist:

- `blackbook_engagement_buckets`
- `blackbook_interest_ids`
- `blackbook_interest_match`

These criteria are dynamic. NJS does not convert them into a permanent contact list when the plan is saved. BlackBook resolves the rule again whenever the audience is previewed and again immediately before Mailchimp distribution.

## Matching semantics

Within Smart Audience:

1. selected engagement buckets use OR;
2. selected interests use ANY or ALL;
3. when both groups are present, engagement AND interest must match.

Smart Audience is then unioned with selected BlackBook segments and explicit people. BlackBook's eligibility/suppression checks are always applied last.

`All eligible` remains an exclusive shortcut and clears other targeting criteria when saved.

## Live preview

The Audience workspace now refreshes the exact BlackBook preview as targeting controls change. Preview includes:

- selected count;
- eligible count;
- subscribed count;
- analyzed count;
- average relationship strength;
- audience categories;
- engagement-bucket distribution.

## BlackBook bridge v2

Newsletter Marketing Bridge v2 extends the existing audience resolver used by both preview and distribution. It queries:

- `marketing.engagement_intelligence.bucket` on people;
- `marketing_person_interests` for canonical-interest membership;
- `marketing_interest_catalog` for selected interest metadata.

No BlackBook CRM records or Mailchimp credentials are migrated.

## AI control plane

Tool count remains 127. The two existing audience tools gain the following inputs:

```json
{
  "engagement_buckets": ["high", "medium"],
  "interest_ids": ["<canonical-interest-id>"],
  "interest_match": "any"
}
```

Syntal AI contract version is `3.9.2`.

## Compatibility

- In-place upgrade from v3.9.1.
- Existing newsletter schedules continue to work.
- Existing segment/person/all-eligible targeting is unchanged.
- Existing Engagement Intelligence data is reused.
- No persistent-volume migration is required.
