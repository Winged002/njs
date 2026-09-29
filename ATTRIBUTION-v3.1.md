# v3.1.0 First-Party Attribution

## What changed

v3.1 turns NJS analytics from traffic reporting into outcome attribution. Each new public view can carry the identifiers of the content and recipe that produced it: Campaign, Product set, Newsjack Worker, source hook/story, domain, page/article, channel and UTMs.

The browser records engagement and interaction signals only when the organization's privacy mode permits it. Confirmed article-response leads are recorded on the server after NJS accepts the response.

## Event path

`view -> engaged -> form_start / product_click -> confirmed lead`

Content performance now includes product clicks, confirmed conversions and conversion rate. Separate attribution tables show performance by Campaign, Product, Worker and source story.

## Product attribution

When a reader clicks a URL matching a Product's `product_url` or `cta_url`, the event is classified as a `product_click` for that exact Product. A later confirmed conversion can be directly attributed to that Product when the product click occurred in the same pseudonymous journey. A conversion without a product interaction remains a Campaign/Worker/content conversion and is not falsely credited to every eligible Product.

## Custom conversions

Generated public pages with tracking enabled expose a small first-party helper:

```js
window.njsTrackConversion('demo_request', {
  type: 'lead',       // lead | signup | purchase
  label: 'Demo request',
  value: 0,
  currency: 'EUR'
});
```

Call this only after the business action succeeds, not on the submit-button click.

## Privacy modes

- **Pseudonymous**: first-party random visitor token enables unique visitors and journey attribution.
- **Consent required**: aggregate page loads are counted before consent; persistent visitor IDs and behavioral interaction tracking start only after opt-in.
- **Aggregate only**: page-load counts only; no persistent visitor token and no behavioral journey attribution.

Settings are scoped to the currently selected Syntal organization at `/analytics/settings`.

## Existing data

No destructive migration is required. Existing `analytics_events` remain valid. New v3.1 events simply include more lineage fields and event types. Existing historical views without Product/Worker/source lineage continue to appear in traffic reports but cannot be retroactively attributed.
