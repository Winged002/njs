# Learning Workers — v3.3.0

## Purpose

A Newsjack Worker is no longer only a fixed recipe. It can learn from the outcomes of the articles it produced while preserving strict editorial and factual constraints.

The loop is:

`Sources → Campaign → Products → Match → Article → Attribution → Learning → Better future ranking`

Learning never creates new claims, changes Campaign evidence, expands the eligible Product pool, or bypasses relevance.

## Modes

### Observe
Measures outcomes and builds a learning report. Matching behavior does not change.

### Recommend
Measures outcomes and presents reviewable recommendations. A user may explicitly apply a recommended base confidence threshold.

### Adaptive
After the configured minimum sample is reached, the Worker can use bounded historical signals automatically. Adaptation is intentionally small and acts as a tie-breaker around the existing recipe.

## What is learned

The Worker evaluates its own attributed public traffic over the configured lookback period:

- source/feed performance;
- Product click and directly attributable conversion performance;
- match-confidence bands;
- story freshness bands;
- repeated story-title terms associated with stronger or weaker outcomes.

The optimization objective can be one of:

- confirmed conversion rate;
- Product click-through rate;
- engaged-view rate.

## Adaptive boundaries

Adaptive behavior may:

- slightly tighten or relax the effective confidence threshold within a narrow range;
- add a small source-performance bias;
- use positive/negative historical topic terms as tie-breakers;
- use Product performance only as a tie-breaker between already relevant eligible Products;
- skip stories outside a learned freshness window when enough evidence supports it.

Adaptive behavior may not:

- turn a non-match into a match without the core relevance model accepting it;
- select a Product that is not configured on the Worker;
- invent Product capabilities or proof;
- replace Campaign or supplemental Collection evidence;
- distort the source story;
- treat historical performance as factual evidence about the current event.

## Auditability

Every generated hook/queue/article records:

- Worker learning mode;
- configured and effective match threshold;
- bounded adjustment and its reasons;
- policy snapshot active when the story was accepted.

This allows later inspection of whether learning influenced a specific generated article.

## Learning report

Open a Worker and choose `Learning` to see:

- sample size and readiness;
- engagement, Product CTR, and conversion performance;
- source performance;
- Product performance;
- confidence-band performance;
- freshness performance;
- positive and weak recurring topic signals;
- current bounded policy;
- human-reviewable recommendations.
