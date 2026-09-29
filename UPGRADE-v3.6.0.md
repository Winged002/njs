# Upgrade to NJS v3.6.0

v3.6.0 preserves the existing NJS MongoDB/Redis volumes and `.env` file. Do not use `docker compose down -v`.

## NJS application

Copy the v3.6.0 release over `/opt/newsjacking-core`, restore the production `.env`, then rebuild `web`, `worker` and `beat`.

## BlackBook bridge

The direct audience/Mailchimp integration requires the companion bridge in the same release.

On the server, from the extracted NJS v3.6.0 release directory:

```bash
cp /opt/blackbook/core/app.py /opt/blackbook/core/app.py.bak-before-njs-newsletter-bridge
python3 deploy/install-blackbook-newsletter-bridge.py /opt/blackbook/core/app.py
python3 -m py_compile /opt/blackbook/core/app.py
```

Then rebuild/restart the BlackBook web/worker services using its existing Compose project.

The installer is idempotent: running it again updates the marked bridge block instead of duplicating routes.

## Existing BlackBook connector

NJS continues to use the organization-scoped BlackBook connector configured under **Integrations → BlackBook**. No Mailchimp secret is added to NJS.

If BlackBook is connected but the bridge is not installed, Newsletter Studio remains usable for planning/building/review/export and clearly shows the BlackBook bridge as unavailable rather than failing the whole newsletter workspace.
