# NJS v3.7.1 — AI Control Merge

v3.7.1 corrects the v3.7.0 branch baseline and expands NJS as a first-party Syntal AI target.

## Merge baseline

v3.7.0 was branched from v3.6.0 and therefore restored older Newsletter Studio templates and CSS. v3.7.1 keeps the v3.7.0 AI control plane and backend behavior while restoring the v3.6.2 Newsletter UI structures for Overview, Plan, Audience, Build, Review, edition detail, Distribute, and Learn.

The newsletter generation `_id` preservation fix, BlackBook Marketing Bridge, Mailchimp handoff, SSO organization switcher and organization-scoped data model remain intact.

## AI control plane: 107 tools

The authenticated manifest now exposes 107 concrete NJS tools. v3.7.1 adds 51 capabilities to the 56 from v3.7.0, including:

- control-plane readiness and capability introspection;
- collection sources and extracted evidence items;
- feed refresh and feed-item visibility;
- Newsjack Worker run history, learning reports, refresh and bounded recommendation application;
- article notes, preserved revisions, revision restore, change jobs and audience responses;
- social automation settings and generation-job visibility;
- BlackBook marketing context, people search, reusable segments and previous campaign context;
- newsletter audience configuration, audience refresh/preview and compiled edition preview;
- first-party analytics event, content and campaign drill-down;
- domain connection, DNS verification, HTTPS activation, route management and disconnect;
- website-site and landing-page review/change-job visibility;
- full controlled-experiment lifecycle: list/read/create/update/start/pause/complete/delete/results.

All tools operate on the same NJS Mongo documents, Celery tasks and organization mappings as the normal UI. There is no AI-only datastore.

## Operator visibility

A new **Analytics → AI Control** screen exposes the current tool catalog, risk classes and discovery endpoints to signed-in NJS operators. Tool execution still requires Syntal AI's forwarded bearer token and the `X-Syntal-AI: 1` marker.

## Runtime identity

The Compose project and containers now use stable names (`syntal-njs`, `syntal-njs-web`, `syntal-njs-worker`, etc.) so app versions no longer become container identities.

For a zero-copy upgrade from v3.7.0, the default persistent volume names intentionally continue to point at the existing `syntal-njs-v370-*` data volumes. They are also environment-overridable with `NJS_MONGO_VOLUME`, `NJS_REDIS_VOLUME`, `NJS_GENERATED_MEDIA_VOLUME`, and `NJS_COLLECTION_FILES_VOLUME`.
