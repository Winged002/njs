# NJS v3.7.1 — Syntal AI Control Plane

## Discovery

Authenticated manifests:

- `GET /.well-known/syntal-ai-tools`
- `GET /api/ai/v1/manifest`

The manifest reports the live `tool_count`, risk counts, group counts and the tool definitions themselves. v3.7.1 ships 107 tools.

## Authorization boundary

Every AI API call continues to require:

1. `X-Syntal-AI: 1`;
2. the signed-in user's forwarded SSO bearer token;
3. live SSO userinfo validation;
4. agreement between forwarded actor user/org headers and the token;
5. `njs.access` for reads or `njs.admin` for mutations/external/destructive actions;
6. resolution of the Syntal organization to the same local NJS workspace used by the browser UI;
7. organization-scoped Mongo operations in NJS.

Syntal AI's risk/approval layer is additive. NJS still enforces authorization itself.

## Tool surface

The control plane covers workspace state, campaigns, products, Collections/evidence, RSS sources, Newsjack Workers and learning, article review/revisions/audience responses, social generation and scheduling, Newsletter Studio/BlackBook/Mailchimp, first-party analytics, domains/routes, websites/landing pages, experiments, and control-plane introspection.

External generation, ingestion, DNS/TLS activation, publishing/distribution and other outward actions remain marked `external`. Archive/delete operations remain marked `destructive`.

## BlackBook

NJS does not receive Mailchimp credentials. BlackBook remains the authority for the people directory, eligibility, enrichment/engagement intelligence, segments, campaign history, recipient synchronization and Mailchimp actions. NJS accesses these through the organization-scoped BlackBook Marketing Bridge.

## Operator screen

`/ai-control` is available through **Analytics → AI Control** for signed-in NJS users. It documents the live catalog but does not provide a browser-side bypass into the tool API.
