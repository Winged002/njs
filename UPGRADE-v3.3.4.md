# Upgrade to v3.3.4

v3.3.4 is a non-destructive Syntal-family UI release on top of v3.3.3.

## Direction

The authenticated product shell is redesigned to align first with Syntal SSO and second with the latest BlackBook interface.

- white/light primary and secondary navigation
- Syntal-style organization switcher in the secondary navigation
- restrained green active states and focus states
- compact flat panels and forms
- BlackBook-style dense operational tables
- consistent page headers and action bars
- existing submenu scroll persistence retained

No destructive MongoDB migration is required. Preserve `.env`, Mongo/Redis volumes, uploads, generated assets, SSO configuration, domains, and TLS state. Rebuild web, worker, and beat after deployment.
