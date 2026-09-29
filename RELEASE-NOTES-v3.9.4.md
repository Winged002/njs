# NJS v3.9.4 — Failed Engagement Queue + 2,500 Contact Batches

v3.9.4 builds on v3.9.3 Prompt Newsletter Design Studio and hardens the BlackBook/Mailchimp Engagement Intelligence workflow for large audiences.

## Failed is now a terminal engagement-processing state

In previous releases, a per-contact Mailchimp/API failure could leave that person in the Engagement Intelligence Queue. The batch itself continued, but the failed contact looked pending and could be selected again unintentionally.

v3.9.4 changes that behavior. Any per-contact import or analysis exception is recorded as `bucket=failed` and `status=failed`. The person immediately stops matching the Queue query while the remaining per-person chains continue normally.

The failure record includes:

- the original error message;
- `failure_code`;
- `failure_stage` (`import` or `analysis`);
- `failed_at`;
- the originating `batch_id`;
- a failure counter.

Mailchimp `403`, `Access Denied`, and `Forbidden` responses are normalized to `mailchimp_access_denied`. Common 401, 429 and timeout failures also receive stable failure codes.

## Failed UI

Engagement Intelligence now has a dedicated **Failed** tab and count. Failed contacts are not included in Queue counts. Operators can inspect the failure reason and explicitly retry selected failed contacts.

Retries are deliberate: a failed contact remains in the Failed state while the new attempt is running and only leaves Failed after a successful classification into Inactive, Low, Medium, or High.

## Batch behavior

One contact failing does not stop the batch. Each contact continues to run as an independent import -> analyze chain. Completed batches can have `status=partial` and report both `failed` and the legacy-compatible `errors` count.

Batch progress still reaches 100% because a failed contact is considered processed, not pending.

## Bulk limit increased to 2,500

The per-batch selection ceiling is now **2,500 contacts** across:

- NJS Engagement Intelligence UI;
- NJS form validation;
- NJS BlackBook client;
- BlackBook Engagement Intelligence bridge;
- BlackBook fan-out orchestration;
- Syntal AI `njs.engagement.bulk_analyze` schema.

The BlackBook bridge defaults `NJS_ENGAGEMENT_MAX_SELECT` to 2500 and the v4 installer updates an existing BlackBook `.env` override to `NJS_ENGAGEMENT_MAX_SELECT=2500`.

## Syntal AI

Tool count remains **127**.

`njs.engagement.people.list` now accepts the `failed` state and returns failure metadata. `njs.engagement.bulk_analyze` accepts up to 2,500 person IDs.
