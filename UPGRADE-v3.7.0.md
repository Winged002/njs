# Upgrade to NJS v3.7.0

v3.7.0 is an additive application/API upgrade over v3.6.0. Existing NJS Mongo documents and `.env` settings remain compatible.

## Important runtime change

v3.7.0 intentionally changes the Compose identity from `newsjacking-core` to `syntal-njs-v370` and assigns explicit container names. It also uses new release-specific Mongo, Redis, generated-media, and collection-file volumes.

Because the volume names are new, do **not** simply start v3.7.0 and discard the old stack. Back up and migrate the existing Mongo database and persisted generated/collection files first. Do not use `docker compose down -v` on the existing NJS deployment.

A safe server upgrade should:

1. Preserve the production `.env`.
2. Identify the exact old NJS web/worker/beat/Mongo/Redis containers by Compose labels.
3. Create a `mongodump` archive from the existing NJS Mongo container.
4. Back up `/app/static/generated` and `/app/data/collections` from the existing NJS volumes.
5. Start only the new `syntal-njs-v370-mongo` and `syntal-njs-v370-redis` containers.
6. Restore the Mongo archive into the new Mongo volume.
7. Stop only the old NJS web/worker/beat processes that conflict with the NJS host port; do not remove unrelated `core-*` containers.
8. Start the new isolated web/worker/beat services and restore generated/collection files into the new persistent volumes.
9. Verify `/health` and the authenticated AI manifest.

## AI verification

Without a Syntal AI bearer token the manifest should return `401`/`403`, not `404`:

```bash
curl -sS -o /tmp/njs-ai-manifest -w 'HTTP %{http_code}\n' \
  https://njs.syntal.pro/.well-known/syntal-ai-tools
```

After the Syntal AI user is signed in, open **Tools → Refresh connections** in `ai.syntal.pro`. NJS should be shown as connected and the app-native tool count should be 56 for a user with `njs.admin` plus `njs.access`.
