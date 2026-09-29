# Upgrade to v3.2.0

v3.2.0 is an additive upgrade over v3.1.0. It does not require a destructive MongoDB migration.

## What changes

- adds the `experiments` MongoDB collection and indexes;
- adds experiment/variant fields to new analytics events;
- adds experiment/variant fields to new article-response submissions;
- adds the Analytics → Experiments workspace;
- adds render-time variants to published pages/articles on both NJS and verified custom domains;
- freezes the published target revision used as each experiment Control and pauses allocation if that target is republished;
- preserves all v3.1 analytics, v3.0.4 Products/Workers and v3.0.3 website-builder behavior.

`ensure_indexes()` creates the new indexes automatically when the web/worker process starts.

## Deployment

Preserve `.env`, persistent Mongo/Redis volumes, uploads and generated media when copying this release over v3.1. Rebuild the web/worker/beat image so all processes run the same v3.2.0 code.

## First test

1. Open Analytics → Experiments.
2. Create a draft against a published page or signed article.
3. Preview Control and Challenger.
4. Start the experiment.
5. Open the public target in separate private sessions and verify variant allocation.
6. Confirm experiment-level views and downstream actions appear on the experiment detail page.
7. Pause and confirm the original published asset is served.
8. Resume or lock a winner.
