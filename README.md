# Newsjacking Core v3.9.5.3 — Monolith Media Contract

Newsjacking Core v3.9.5.3 restores compatibility with the legacy monolith article-image contract while retaining the newer Collection/OpenAI media-generation pipeline.

Generated articles now persist a real cover image in the canonical legacy location, mirror it into the newer hero-image field, and place verified image URLs directly into rendered article content.

This release builds on the Engagement Intelligence, newsletter design, Collections, and Syntal AI capabilities introduced throughout the v3.9.x line.

## v3.9.5.3 highlights

- Restores the legacy monolith article image contract.
- Generated articles persist a real cover image under:

```text
metadata.image_info.url
```

- The same cover image is mirrored to:

```text
metadata.hero_image
```

- Verified generated image URLs are injected directly into:

```text
article.content
```

- Existing Collection/OpenAI media generation remains supported.
- Article consumers that depend on the older `metadata.image_info.url` contract work without abandoning the newer media pipeline.
- Generated article content receives usable media URLs rather than relying only on detached metadata.
- Media persistence is normalized across legacy monolith and newer Collection-based article workflows.

## Article media contract

The canonical generated article media relationship is now:

```text
Generated media
    │
    ├── metadata.image_info.url
    │       Legacy monolith cover-image contract
    │
    ├── metadata.hero_image
    │       Mirrored hero-image reference
    │
    └── article.content
            Verified image URLs embedded into rendered content
```

A generated article should therefore expose its primary media through both legacy and current metadata paths.

The image referenced from article content must correspond to a verified generated-media URL rather than an unresolved generation request, temporary placeholder, or unsupported internal reference.

## Compatibility

v3.9.5.3 is specifically intended to maintain interoperability between two generations of NJS article media handling:

**Legacy monolith consumers**

```text
metadata.image_info.url
```

**Current NJS media consumers**

```text
metadata.hero_image
Collection/OpenAI media pipeline
article.content
```

The compatibility layer does not replace the newer Collection/OpenAI media architecture. It restores the media fields expected by existing monolith article rendering and downstream integrations.

---

# Newsjacking Core v3.9.4 — Failed Engagement Queue

NJS v3.9.4 introduced a dedicated terminal failure state for Engagement Intelligence.

It builds directly on the v3.9.3 Prompt Newsletter Design Studio, retaining the prompt-driven newsletter visual-design workflow while ensuring that per-contact Mailchimp/API failures no longer remain indefinitely in Queue.

## v3.9.4 highlights

- Engagement Intelligence has a dedicated **Failed** tab and metric.
- A contact that fails import or analysis is removed from Queue and persisted as:

```text
bucket=failed
```

- Mailchimp `403`, `Access Denied`, and `Forbidden` errors are normalized to:

```text
mailchimp_access_denied
```

- Failure message, stage, timestamp, batch ID and failure count are retained for review.
- Other contacts in the same batch continue normally.
- Partial batches can complete to 100% while reporting their failed count.
- Failed contacts can be deliberately selected and retried from the Failed tab.
- Bulk engagement analysis supports up to **2,500 contacts** per batch end-to-end.
- The BlackBook bridge v4 installer ensures:

```text
NJS_ENGAGEMENT_MAX_SELECT=2500
```

in the BlackBook `.env`.

## Newsletter visual design

The newsletter design capabilities introduced in v3.9.3 remain available.

Generated newsletter editions include the prompt-driven **Visual design** workspace. Operators can redesign an email using natural-language instructions while preserving its stories, links, and factual content.

The workflow supports:

- desktop preview;
- mobile preview;
- non-destructive design revisions;
- restoration of previous design state;
- advanced HTML source editing;
- reusable visual direction for future editions.

## Engagement-state model

Engagement Intelligence separates processing state from successful engagement classification.

```text
queue
    Waiting to be analyzed

failed
    Latest per-contact attempt terminated with an exception

inactive
    Successfully processed with no meaningful engagement

low
    Successfully classified as low engagement

medium
    Successfully classified as medium engagement

high
    Successfully classified as high engagement
```

`failed` is intentionally not treated as a Smart Audience engagement segment.

Newsletter audience targeting continues to use the four successfully processed engagement buckets:

```text
Inactive
Low
Medium
High
```

Failed contacts remain available separately for inspection and deliberate retry.

## Syntal AI

The v3.9.4 Syntal AI contract exposes **127 tools**.

Relevant Engagement Intelligence capabilities include:

```text
njs.engagement.people.list
```

Supports:

```text
bucket=failed
```

and returns associated failure metadata.

```text
njs.engagement.bulk_analyze
```

Accepts up to **2,500 person IDs**.

Newsletter visual-design tools introduced with v3.9.3 remain available unchanged.

Contracts:

```text
config/njs-v3.9.4-contract.json
NJS-AI-MANIFEST-v3.9.4.json
```

## v3.9.4 upgrade requirements

v3.9.4 requires the bundled BlackBook Engagement Intelligence bridge v4 because failed-state persistence and 2,500-contact batch enforcement are implemented through the BlackBook worker/API path.

Install with:

```bash
python3 deploy/install-blackbook-engagement-intelligence-v4.py /opt/blackbook/core
python3 deploy/install-syntal-ai-v394-contract.py /opt/syntal-ai/core
python3 deploy/check-v3.9.4.py
```

Do not remove persistent Docker volumes during the upgrade.

Related documentation:

```text
RELEASE-NOTES-v3.9.4.md
ENGAGEMENT-INTELLIGENCE-v3.9.4.md
UPGRADE-v3.9.4.md
NEWSLETTER-DESIGN-v3.9.3.md
```

## Release lineage

```text
v3.9.3
└── Prompt Newsletter Design Studio

v3.9.4
├── Failed Engagement Queue
├── Mailchimp failure normalization
└── 2,500-contact bulk engagement analysis

v3.9.5.x
├── Continued article/media pipeline work
├── Collection/OpenAI media generation
└── v3.9.5.3 Monolith Media Contract
    ├── metadata.image_info.url
    ├── metadata.hero_image
    └── verified media URLs in article.content
```

## Persistent-data safety

NJS upgrades in this release line are intended to preserve existing persistent application data.

Do not remove production Docker volumes as part of a normal application upgrade unless a release-specific migration procedure explicitly requires it.
