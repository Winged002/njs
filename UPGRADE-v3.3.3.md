# Upgrade to v3.3.3

v3.3.3 is a non-destructive professional UI redesign on top of v3.3.2.

## What changes

- Replaces the glass-heavy v3.3.1/v3.3.2 visual treatment with a restrained BlueBook-style professional system
- Keeps the persistent two-layer navigation and v3.3.2 saved submenu scroll positions
- Tightens page density, typography, controls, cards, tables, forms, review surfaces, and builders
- No destructive MongoDB schema migration

## Deployment

Preserve `.env`, Mongo/Redis volumes, uploads, generated media, custom-domain state, TLS, and SSO configuration. Rebuild web, worker, and beat so every process reports v3.3.3.
