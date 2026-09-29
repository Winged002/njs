# v3.0.2 interface audit

The v3.0.2 pass treats the UI as one product rather than a set of isolated screens.

## Global shell
- Primary and contextual navigation share one spacing and active-state system.
- Distribution replaces the misleading top-level Social label because the area includes social posts, scheduling and newsletters.
- Active Syntal workspace is visible in the top bar with an explicit Switch affordance.
- Global Create menu exposes campaign, collection, landing-page and source entry points.
- Account controls are compact and remain separated from workspace operations.
- Desktop, tablet and phone navigation have distinct, intentional layouts.

## Interaction system
- Consistent primary, secondary, quiet and destructive action hierarchy.
- Standard control heights, borders, focus rings and disabled states.
- Clear pending state remains wired to form submission.
- Visible keyboard focus, skip-to-content navigation and reduced-motion support.
- Tables use stable headers and horizontal overflow rather than compressing columns.
- Empty, warning, error and live-processing states use one visual language.

## Creation flows
- Campaign evidence uses one flexible text column with optional trailing media; text never occupies a narrow middle column.
- Landing-page evidence follows the same structural rule.
- Required choices are shown before advanced configuration.
- Sticky completion bars remain visible on desktop and return to normal stacked flow on small screens.

## Operational surfaces
Reviewed styling and responsive behavior for Dashboard, Campaigns, Collections, Sources, Newsjacking, Articles, Article Review, Author Profile, Websites, Domains, Analytics, Distribution Calendar, Distribution Queue, Social Posts, Social Settings, Newsletters, Newsletter Editions, BlackBook integration, login and error states.

## Compatibility
No MongoDB schema change. No Syntal OIDC contract change. No change to persistent Docker volumes. No change to the automatic custom-domain provisioner. Existing v3.0 organization scoping remains intact.
