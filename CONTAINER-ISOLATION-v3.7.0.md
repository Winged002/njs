# NJS v3.7.0 container isolation

The release does not rely on the directory basename as the Compose project name. `compose.yml` explicitly declares:

```yaml
name: syntal-njs-v370
```

Each runtime container also has an explicit name. This ensures NJS cannot accidentally become `core-web-1`, `core-worker-1`, `core-mongo-1`, etc. when installed in `/opt/.../core`.

The web host port remains `${HOST_PORT:-8010}`, so Nginx can continue proxying the existing `njs.syntal.pro` upstream. During the migration, the old NJS web container must be stopped immediately before the new v3.7 web container is started because only one container can bind that host port.
