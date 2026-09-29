# v3.0.4 Products and Newsjack Workers

## Content model

The application now separates four reusable concerns:

1. **Collections** — source material, facts and evidence.
2. **Campaigns** — a curated evidence selection plus audience, objective and editorial direction.
3. **Products** — the offer being promoted, including verified product context, destination URL, CTA, positioning, proof points and claims that must not be made.
4. **Newsjack Workers** — independent recipes that select Sources, one Campaign, an eligible Product pool, optional supplemental Collection evidence and matching/placement rules.

The intended generation equation is:

`news story + campaign context + optional worker evidence + relevant product = reviewable article`

## Product relevance

A Product is eligible, not mandatory. The relevance matcher must find a concrete audience reason to care about the story and a defensible relationship between the story and at least one Product. It normally selects one Product and can select at most two. If the bridge would be forced, the Worker skips the match rather than manufacturing a promotional angle.

The default **Subtle** placement mode means editorial restraint, not concealed advertising: the news and practical value lead, Product facts are limited to supplied evidence, no independent endorsement is implied, and the CTA is proportionate to the article.

## Independent Workers

Workers are intentionally independent. For example:

- Worker A: Sources A + C → Campaign A → Products B + C
- Worker B: Sources B + C → Campaign B → Products A + C

A shared Source item can therefore be evaluated separately by both Workers. Deduplication includes the Worker ID so one Worker's processing does not suppress another Worker's valid recipe.

## Traceability

Generated articles retain the selected Worker, Campaign, Products, source research, product bridge and supplemental Worker evidence snapshot. Review-note revisions receive the same bounded context so later edits cannot silently introduce unsupported Product claims.

## Migration behavior

Existing Campaigns, Collections, articles, sites and domains are unchanged. Legacy Newsjacking automation remains available for workspaces with no Workers. As soon as a workspace contains a Worker, scheduled Newsjacking uses the Worker model instead of running the legacy recipe in parallel.
