# NJS v3.7.1 — Stable runtime identity

v3.7.1 separates application versioning from Docker runtime identity:

- project: `syntal-njs`
- web: `syntal-njs-web`
- worker: `syntal-njs-worker`
- beat: `syntal-njs-beat`
- Mongo: `syntal-njs-mongo`
- Redis: `syntal-njs-redis`
- image: `syntal-njs-app:3.7.1`

The persistent volume identifiers intentionally default to the existing v3.7.0 names so an upgrade can reuse the current database and generated files without a copy. Future app releases should keep the same persistent identifiers (or the same `NJS_*_VOLUME` overrides) while changing only the image/application version.
