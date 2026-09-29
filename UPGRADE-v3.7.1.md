# Upgrade to NJS v3.7.1

v3.7.1 is an in-place application upgrade over v3.7.0 and is data-model compatible.

## Data volumes

The Compose project/container names become stable `syntal-njs-*` names. To avoid copying the v3.7.0 Mongo, Redis, generated-media and Collection data again, the v3.7.1 Compose defaults intentionally reuse the existing v3.7.0 volume names:

- `syntal-njs-v370-mongo-data`
- `syntal-njs-v370-redis-data`
- `syntal-njs-v370-generated-media`
- `syntal-njs-v370-collection-files`

Future versions can keep using these exact volumes even as application image versions change. The names are overrideable with the four `NJS_*_VOLUME` environment variables.

Do not run `docker compose down -v` on either release.

## Upgrade sequence

1. Preserve `.env` and back up Mongo/source.
2. Stop the old v3.7.0 web/worker/beat/mongo/redis containers before starting the stable-name stack, because the new Mongo container intentionally mounts the same persistent database volume.
3. Install v3.7.1 source and restore `.env`.
4. Build `syntal-njs-app:3.7.1`.
5. Start the stable `syntal-njs-*` stack.
6. Verify `/health`, Newsletter pages and authenticated AI manifest.

The BlackBook bridge from v3.6.0 remains compatible and does not need to be reinstalled for this NJS-only release.
