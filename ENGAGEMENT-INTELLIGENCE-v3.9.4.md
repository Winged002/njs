# Engagement Intelligence v3.9.4

The v4 BlackBook bridge introduces an explicit processing-state separation:

- `queue`: not yet successfully processed and not terminally failed;
- `failed`: the latest per-contact import/analysis attempt ended in an exception;
- `inactive`: imported successfully but no meaningful engagement was found;
- `low`, `medium`, `high`: successfully analyzed engagement classifications.

`failed` is a workflow state, not a newsletter-audience engagement segment. Smart Audience continues to use only Inactive, Low, Medium and High for behavioral targeting.

## 403 handling

A Mailchimp response containing HTTP 403, `Access Denied`, or `Forbidden` is persisted with:

```text
bucket=failed
status=failed
analysis_status=failed
failure_code=mailchimp_access_denied
```

The full sanitized exception text is retained in `marketing.engagement_intelligence.error` and `marketing.ai_analysis_error` for operator review.

## Queue semantics

Queue counts and Queue list queries only match contacts where the engagement bucket is missing or explicitly `queue`. Failed contacts therefore cannot remain visually stuck in Queue after a completed attempt.

Refreshing the Mailchimp audience updates membership/status/rates, but it does not automatically requeue failed contacts. Retry is explicit from the Failed tab or through the existing bulk-analyze API.

## Large batches

The default and hard maximum are both 2,500 selected contacts per bulk-analysis request. The existing parallel per-person chain architecture is retained, so increasing the selection limit does not turn one contact failure into a batch failure.
