# Engagement Intelligence v3.9.1

## Processing model

A submitted selection of 1-1000 contacts creates one parent batch. The parent task only dispatches work; it does not import the entire Mailchimp audience or loop through people itself.

Each person receives a Celery chain:

```text
blackbook.njs_engagement_person_import
              |
              v
blackbook.njs_engagement_person_analyze
```

All person chains are dispatched as a group. `blackbook.njs_engagement_batch_finalize` is a chord callback that completes the batch after every chain returns.

## Import stage

The import stage first checks already-imported BlackBook marketing activity. If meaningful open/click events already exist, it does not make unnecessary Mailchimp calls.

If local activity is empty, it uses the Mailchimp member rates stored by queue refresh. Zero open and click rates immediately classify the contact as inactive. If rates are unavailable, one member lookup fills them.

Only contacts with positive engagement signals receive detailed per-person activity import using `mailchimp_import_person_activity`.

Default detailed campaign limit:

```dotenv
NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT=20
```

## AI stage

AI is never invoked for zero-engagement contacts.

For engaged contacts, the AI receives only the selected person's behavior and an existing canonical-interest catalog snapshot. No other contacts are loaded into the prompt or used as comparison examples.

The catalog snapshot limit defaults to 250 entries:

```dotenv
NJS_ENGAGEMENT_CATALOG_AI_LIMIT=250
```

Local canonical matching can inspect up to 750 recent catalog entries and reuses equivalent interests before creating a new one.

## Parallelism

BlackBook worker concurrency defaults to 6 after running the v3 installer. This is deliberately moderate because each active contact may make several Mailchimp API calls and one AI request.

For a small batch of 3-5 contacts, all chains can normally be active concurrently. For 1000 selected contacts, Celery naturally drains the group according to worker concurrency without one monolithic task holding the worker.
