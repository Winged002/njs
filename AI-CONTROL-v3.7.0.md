# NJS v3.7.0 — Syntal AI Control Plane

NJS v3.7.0 publishes a first-party Syntal AI tool manifest at both:

- `GET /.well-known/syntal-ai-tools`
- `GET /api/ai/v1/manifest`

The manifest is authenticated. Syntal AI forwards the signed-in user's SSO bearer token and the actor organization/user headers. NJS calls Syntal SSO userinfo on every AI request, verifies the actor headers against the returned identity, resolves the Syntal organization to the existing local NJS workspace, and then applies that organization boundary to every Mongo query.

## Permission model

The release deliberately works with the SSO catalog already deployed:

- read tools require `njs.access`;
- mutation tools require `njs.admin`;
- the manifest labels operations as `read`, `write`, `external`, or `destructive`, allowing Syntal AI to apply its confirmation policy before NJS receives the call.

NJS still performs its own authorization. AI discovery is not authorization.

## Tool coverage

v3.7.0 exposes 56 first-party tools across the core operating surfaces:

- workspace summary;
- campaign list/read/create/update/strategy preparation;
- product list/read/create/update/delete;
- evidence collection list/read/create/note ingestion/URL ingestion;
- RSS feed list/create/enable-disable/delete;
- newsjacking worker list/read/create/update/run/enable-disable/delete;
- article list/read/request-changes/sign-and-publish;
- social post list/read/edit/schedule/archive and article social generation;
- newsletter schedule list/read/create/update/generate/archive;
- newsletter edition list/read/edit/mark-ready/archive and BlackBook/Mailchimp distribution;
- analytics summary;
- publishing domain, website site, and landing-page visibility plus landing-page generation;
- BlackBook connector status and audience preview.

These tools operate on the same Mongo collections and Celery tasks used by the regular NJS UI. There is no parallel AI datastore.

## External and destructive actions

Actions that can generate content, fetch external sources, schedule outward-facing content, publish an article, generate social content, generate a newsletter, generate a landing page, or hand a newsletter to BlackBook/Mailchimp are marked `external`.

Deletes and archive operations are marked `destructive`. Syntal AI can therefore require explicit operator approval before invoking them.

## BlackBook / Mailchimp

v3.7.0 preserves the v3.6.0 BlackBook Marketing Bridge. The AI distribution tool does not receive or store Mailchimp credentials. It calls the same organization-scoped NJS → BlackBook bridge used by Newsletter Studio. A distribution request can either create a Mailchimp draft or explicitly send, depending on the approved `action` argument.

## SSO organization boundary

The AI request must carry the same Syntal user and organization as the SSO bearer token. NJS rejects mismatches. All normal content collections are then scoped by the linked local `organization_id`, matching the regular multi-organization NJS behavior.

## Container isolation

The v3.7.0 Compose project is `syntal-njs-v370`. Its runtime names are:

- `syntal-njs-v370-web`
- `syntal-njs-v370-worker`
- `syntal-njs-v370-beat`
- `syntal-njs-v370-mongo`
- `syntal-njs-v370-redis`

The shared application image is `syntal-njs-v370-app:3.7.0` and the networks/volumes use the same release-specific prefix. This prevents the generic `core-*` ownership problem seen when several Syntal applications are deployed from directories named `core`.
