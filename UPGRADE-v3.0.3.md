# Newsjacking Core v3.0.3 upgrade notes

v3.0.3 upgrades Websites from independent generated pages to shared multi-page websites.

## What changes

- Each connected domain receives a `website_sites` workspace with a shared design system and navigation manifest.
- The homepage becomes the preferred style source. Existing v3.0.2 homepages are used as a visual migration reference when the first new page is generated.
- New About, Services, Contact, Resources, News and custom pages inherit the website design system instead of inventing a new visual identity.
- Shared navigation and footer chrome are injected at render time, so enabling a new page updates the homepage and all sibling pages immediately.
- Secondary pages are intentionally kept out of navigation until reviewed. The UI offers `Publish + add to navigation` as a single explicit action.
- Home/Resources/News pages can include the dynamic article collection; About/Services/Contact pages no longer receive it by default.
- Review-note AI revisions are constrained to the shared website design system.

## Data compatibility

No existing campaign, article, domain route, landing page, SSO organization, analytics event or persistent volume is removed. The new `website_sites` collection is created automatically by `ensure_indexes()`. Existing domain pages are grouped into a site when the Websites workspace is opened. Published legacy secondary pages remain navigation-visible during migration.

## Deployment

Deploy over v3.0.2 using the existing `.env` and persistent volumes, rebuild the Docker images, and restart the stack. Application/image version: `3.0.3`.
