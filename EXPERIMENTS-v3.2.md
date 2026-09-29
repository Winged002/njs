# Experiments — v3.2.0

v3.2 adds controlled experimentation to the first-party attribution system.

## Model

Each experiment belongs to one Syntal organization and targets exactly one published asset:

- `page` — a published generated website page;
- `article` — a signed/public article.

An experiment contains an immutable Control snapshot and a Challenger definition. At creation time NJS freezes the exact published target revision used by Control. Both Control and Challenger render from that frozen revision; the Challenger then applies bounded render-time overrides:

- public headline;
- supporting deck/subheadline;
- CTA label;
- CTA destination;
- CTA placement (`existing`, `after_hero`, `after_content`, `sticky_bottom`).

The original page/article remains unchanged. Pausing the experiment therefore restores normal publishing immediately. If the target is republished while a test is running—or after a winner is locked—NJS pauses experiment allocation instead of silently mixing a new baseline into the old result set. A new experiment should be created for the new published revision.

## Traffic allocation

A visitor receives a small first-party functional cookie containing only the assigned variant ID for that specific experiment. It does not contain a visitor identifier. Existing visitors remain on the same variant when the experiment is paused and resumed. A completed experiment ignores prior allocation and serves the locked winner.

Only one running experiment can allocate traffic on a target at a time. A newer running experiment takes precedence over an older completed winner.

## Measurement

v3.1 analytics events now carry:

- `experiment_id`;
- `variant_id`.

The lineage survives view, engagement, CTA click, Product click and conversion events. Server-confirmed article audience-response submissions also record these fields.

Primary metrics are:

- confirmed conversion rate;
- CTA click-through rate;
- Product click-through rate;
- engagement rate.

The experiment dashboard shows views, unique visitors when privacy mode permits, engagement, Product clicks, confirmed conversions and the selected primary metric per variant.

## Decision support

After both compared variants reach the configured minimum views, NJS calculates a two-proportion normal-approximation confidence indicator for the selected binary metric plus observed lift versus Control. This is decision support, not a guarantee of causality or future performance. The editor remains responsible for choosing when to stop a test.

Locking a winner changes the experiment to `completed` and directs 100% of experiment traffic to the selected variant. It does not rewrite the source asset.

## AI challenger suggestions

Draft/paused experiments can use **Suggest challenger**. The configured DeepSeek model receives:

- the test hypothesis;
- primary metric;
- current title/description;
- a bounded text excerpt;
- known Product/Campaign CTA destinations.

It is instructed to change as little as needed, preserve factual boundaries and use only an allowed CTA destination. The suggestion remains editable and does not start the experiment automatically.

## Privacy

Experiment allocation and analytics privacy are intentionally separate:

- the variant cookie stores only the functional assignment;
- pseudonymous analytics keeps the full measurable journey;
- consent mode does not begin persistent behavioral analytics until consent;
- aggregate mode can still count variant page views, but cannot provide visitor-level browser journeys;
- server-side NJS article-response conversions can retain the variant assignment without requiring a pseudonymous analytics identifier.
