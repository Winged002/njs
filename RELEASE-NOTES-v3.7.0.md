# NJS v3.7.0 — AI Control Plane

v3.7.0 is the Syntal AI control-plane release for NJS. It is based directly on v3.6.0 Newsletter Studio and retains the BlackBook/Mailchimp bridge, guided newsletter workflow, organization switcher, evidence collections, newsjacking workers, social generation, website builder, domains, analytics, learning and review/signing workflows.

## First-party Syntal AI integration

NJS now publishes an authenticated Syntal AI manifest at:

- `/.well-known/syntal-ai-tools`
- `/api/ai/v1/manifest`

The manifest exposes 56 app-native capabilities. Syntal AI v0.4+ can discover them automatically when NJS is enabled for the active SSO organization.

## Authorization

Every AI API call:

1. requires the `X-Syntal-AI: 1` marker;
2. requires the signed-in user's forwarded SSO bearer token;
3. resolves SSO userinfo live;
4. validates the forwarded Syntal user and organization headers against the token;
5. requires `njs.access` for reads or `njs.admin` for mutations;
6. resolves the Syntal organization to the same local NJS workspace mapping used by the web UI;
7. scopes Mongo reads/writes to that workspace.

Tool discovery never bypasses target-app authorization.

## Tool groups

The manifest covers campaigns, products, collections/evidence, feeds, Newsjack Workers, articles/review/signing, social content, newsletters, analytics, domains, sites, landing pages and BlackBook audience intelligence/distribution.

Content generation, external ingestion, scheduling, publishing and Mailchimp handoff are marked `external`. Deletes/archive actions are marked `destructive` so Syntal AI's approval layer can stop before execution.

## Existing NJS data model

The control API writes to the existing Mongo collections and queues the existing Celery tasks. Content created or changed from Syntal AI appears in the normal NJS interface and vice versa.

## Runtime isolation

The Compose project is now explicitly named `syntal-njs-v370` and all containers have fixed NJS-specific names:

- `syntal-njs-v370-web`
- `syntal-njs-v370-worker`
- `syntal-njs-v370-beat`
- `syntal-njs-v370-mongo`
- `syntal-njs-v370-redis`

The app image is `syntal-njs-v370-app:3.7.0`; networks and persistent volumes are release-prefixed as well. This prevents accidental collisions with other applications installed under `/opt/.../core`.

## Upgrade compatibility

Mongo document schemas remain compatible with v3.6.0. Because v3.7.0 intentionally uses new isolated volume names, migrate the existing Mongo and persisted generated/collection files before retiring the previous stack. Never use `docker compose down -v` on the old deployment.
