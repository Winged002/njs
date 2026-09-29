# Upgrade NJS v3.4.0 -> v3.5.0

v3.5.0 is a non-destructive application/UI update. Existing newsletter schedules and editions remain compatible. No MongoDB volume reset is required.

```bash
cd /opt/newsjacking-core
cp .env /root/newsjacking-core.env.backup

tar -czf /root/newsjacking-core-v3.4.0-source-backup.tar.gz \
  --exclude='./.env' \
  --exclude='./__pycache__' \
  .
```

Extract the v3.5.0 archive and copy its contents over `/opt/newsjacking-core`, then restore `.env`.

```bash
cd /opt/newsjacking-core
cp /root/newsjacking-core.env.backup .env

docker compose build --pull web worker beat
docker compose up -d --remove-orphans
```

Verify:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8010/health && echo
```

Expected health version: `3.5.0`.
