# Newsjacking Core v3.0.1 upgrade notes

v3.0.1 is a UI/UX refinement release on top of v3.0. It does not change the deployment model, database schema, SSO configuration, or automatic custom-domain provisioner.

## What changes

- `/campaigns/new` now uses full-width evidence rows so text never collapses into the old middle grid column when no thumbnail is present.
- Evidence selection includes search, selected count, select-visible, clear, and disabled-until-ready submit behavior.
- Website page creation is reorganized into source context, required page identity, then optional advanced direction.
- Newsletter schedule creation emphasizes name, timing, weekdays, and campaign scope; editorial/generation controls are still available under Advanced.
- Collection intake and Newsjacking automation show compact workflow guidance.
- Existing v3.0 organization switching and organization-level data isolation remain unchanged.

## Deployment

Use the same `.env` file and persistent volumes from v3.0.

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs web --tail=120
```

The compose image and application version are `3.0.1`.
