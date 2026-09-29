# Upgrade to v3.3.1

v3.3.1 is a non-destructive UI/UX release on top of v3.3.0.

## What changes

- Glass compact redesign across the app shell and primary workspaces
- Refined cards, forms, metrics, tables, pickers, and sticky workspace controls
- No schema-breaking database migration
- Learning Workers, Experiments, Attribution, Products, and Website Builder remain intact

## Deployment notes

Preserve `.env`, Mongo/Redis volumes, uploads, generated assets, domains, TLS state, and SSO configuration. Rebuild `web`, `worker`, and `beat` so all processes serve the same v3.3.1 assets and templates.
