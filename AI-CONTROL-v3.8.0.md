# NJS v3.8.0 — AI Control Plane

NJS v3.8.0 publishes 120 first-party tool definitions from:

- `GET /.well-known/syntal-ai-tools`
- `GET /api/ai/v1/manifest`

The manifest contains capability metadata only. Executable operations require:

1. `X-Syntal-AI: 1`;
2. the signed-in Syntal AI user's bearer token;
3. successful SSO userinfo identity validation;
4. matching actor user/organization headers when present;
5. live SSO delegation evaluation for `njs.access` or `njs.admin`;
6. NJS organization/workspace ownership checks.

## Landing-page tool model

Human UI and AI tools operate on the same `landing_pages`, `landing_page_versions`, `landing_page_change_jobs`, `website_sites` and `domain_routes` collections.

Manual writes preserve the previous HTML revision before replacing the current working copy. AI review jobs preserve the reviewed revision and run through the existing Celery landing-page revision worker. Publishing snapshots the current HTML/metadata/article list into the published state. Unpublish leaves the draft and revision history intact.

Permanent delete is a destructive tool and requires `confirm=true` at the NJS API in addition to Syntal AI approval policy.

## Contract compatibility

Install the bundled Syntal AI contract after upgrading NJS:

```bash
python3 deploy/install-syntal-ai-v38-contract.py /opt/syntal-ai/core
```

Restart Syntal AI and refresh the NJS connection so its cached contract is reloaded.
