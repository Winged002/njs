# Newsjacking Core v3.0 upgrade notes

v3.0 keeps the v2.9 deployment model and automatic custom-domain provisioner. Rebuild the existing stack with the new package; no new Docker volume is required.

## What changes

- New Bonita UI shell, dashboard and Syntal sign-in experience.
- Active Syntal organization is shown in the top bar.
- **Switch organization** re-enters the Syntal OIDC authorization flow so Syntal remains the source of truth for organization membership.
- A Syntal organization now maps to its own local NJS workspace, including when the same Syntal user belongs to several organizations.
- Workspace queries are organization-scoped rather than user-or-organization scoped.
- RSS feeds, scan history and Newsjacking automation settings are separated by organization.
- Legacy user-owned records without `organization_id` are claimed once into the first active Syntal workspace after the upgrade.

## Deployment

Use the same `.env` values already used by v2.9. The compose image and application version are now `3.0.0`.

```bash
docker compose down
docker compose up -d --build
docker compose ps
docker compose logs web --tail=100
```

After signing in, use the organization control in the top bar to reopen the Syntal organization selector.
