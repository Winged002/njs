# Newsjacking Core v3.0.2 upgrade notes

v3.0.2 is a comprehensive UI/UX refinement release on top of v3.0.1. It does not change the MongoDB schema, Syntal OIDC configuration, persistent volumes, custom-domain provisioner, or organization isolation model.

## Interface changes

- One coherent visual system now governs the application shell, page hierarchy, forms, tables, cards, badges, notices and empty states.
- The top bar includes a global Create menu and a clearer active-workspace switcher.
- Navigation has improved active states, density, truncation behavior and mobile handling.
- Creation flows are optimized for progressive disclosure: essential choices first, advanced configuration second.
- Campaign and landing-page evidence pickers no longer depend on fragile three-column text placement.
- Operational views for Collections, Sources, Newsjacking, Article Review, Websites, Analytics, Social and Newsletters have consistent spacing and action hierarchy.
- Sticky review/submit areas are used only where they improve task completion and fall back to normal flow on narrow screens.
- Keyboard focus, skip navigation, reduced-motion behavior and minimum control sizing are improved.
- Legacy implementation-version badges were removed from visible workflow UI.

## Deployment

Use the existing `.env` and persistent Docker volumes from v3.0/v3.0.1.

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs web --tail=150
```

The compose image and application version are `3.0.2`.
