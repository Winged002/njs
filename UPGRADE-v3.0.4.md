# Newsjacking Core v3.0.4 upgrade notes

v3.0.4 adds Products and independent Newsjack Workers. It is designed to deploy directly over v3.0.3 with the existing `.env`, MongoDB data, uploads, generated assets, Syntal SSO configuration, domain mappings and TLS setup preserved.

## New MongoDB collections

- `products`
- `newsjacking_workers`

Indexes are created by the application's existing database initialization path. No destructive migration is required. Existing organization-scoped records remain unchanged.

## After deployment

1. Open **Products** and create at least one active Product. Attach verified Collection evidence when available.
2. Confirm the Campaign you want to use is active.
3. Open **Newsjacking → New worker**.
4. Select Sources, one Campaign, eligible Products and any optional supplemental Collection evidence.
5. Enable the Worker and run it manually once to verify the recipe.
6. Review generated articles before publishing.

The previous single Newsjacking settings model continues only while a workspace has no Workers. Once the first Worker exists, scheduled scans use Workers and the legacy recipe is not run in parallel.

Application/image version: `3.0.4`.
