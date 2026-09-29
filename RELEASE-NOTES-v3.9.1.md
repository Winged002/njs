# NJS v3.9.1 — Parallel Engagement Pipeline

v3.9.1 fixes the scalability problem in v3.9.0 Engagement Intelligence.

## Root cause in v3.9.0

Every bulk analysis batch first ran two global operations:

- refresh all Mailchimp campaign reports;
- import all Mailchimp activity.

Only after those operations completed did it process the selected people one at a time. On an audience of roughly 16,900 contacts, even a three-person selection could therefore take many minutes. Two concurrent batches could duplicate the same full-audience work.

## New architecture

`blackbook.njs_engagement_bulk_analysis` is now an orchestrator only. It fans out one Celery chain per selected person:

```text
person import/check
    -> zero engagement: inactive, stop
    -> engaged: AI analyze
    -> canonical interest matching
```

A chord callback finalizes the parent batch after every child chain returns.

New BlackBook tasks:

- `blackbook.njs_engagement_person_import`
- `blackbook.njs_engagement_person_analyze`
- `blackbook.njs_engagement_batch_finalize`
- `blackbook.njs_engagement_queue_refresh`
- `blackbook.njs_engagement_bulk_analysis` remains the parent orchestrator.

## Inactive fast path

Queue refresh stores Mailchimp member `avg_open_rate`, `avg_click_rate`, and `member_rating` in BlackBook. If a selected person's stored rates and local activity show no engagement, the person is classified `inactive` without detailed campaign import and without any AI call.

## Engaged-contact path

Only selected contacts with an engagement signal get a per-person Mailchimp activity import. No full-audience campaign/activity import runs during bulk analysis.

AI analysis uses only:

- the selected person's Mailchimp activity;
- deterministic engagement metrics;
- the existing canonical-interest catalog.

It does not compare the selected person to other contacts.

## Interest behavior

Existing canonical interests are preferred. Local canonical matching scans the interest catalog only. New catalog entries are created only when the inferred signal does not match an existing canonical interest.

## Worker parallelism

The companion BlackBook installer changes the worker default from concurrency 2 to a configurable default of 6:

```dotenv
BLACKBOOK_WORKER_CONCURRENCY=6
```

Celery prefetch remains 1 in BlackBook, which is suitable for these variable-duration tasks.

## Compatibility

- NJS AI tool count remains 127.
- Syntal AI contract version becomes NJS `3.9.1`.
- Existing queue/bucket/interest data remains compatible.
- Existing BlackBook Mailchimp credentials and CRM data remain unchanged.
- No persistent-volume migration is required.
