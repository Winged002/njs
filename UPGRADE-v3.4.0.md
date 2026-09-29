# Upgrade to Newsjacking Core v3.4.0

v3.4.0 is a non-destructive UI/branding release on top of v3.3.4. MongoDB and Redis volumes remain compatible and no schema migration is required.

## Syntal-TWO upgrade

```bash
scp ~/Downloads/newsjacking-core-v3.4.0-blue-star-brand.zip root@95.179.253.170:/root/
ssh root@95.179.253.170

cd /opt/newsjacking-core
cp .env /root/newsjacking-core.env.v3.4.0.backup
tar -czf /root/newsjacking-core-v3.3.4-source-backup.tar.gz \
  --exclude='./.env' \
  --exclude='./__pycache__' \
  .

rm -rf /tmp/newsjacking-core-v3.4.0
mkdir -p /tmp/newsjacking-core-v3.4.0
unzip -o /root/newsjacking-core-v3.4.0-blue-star-brand.zip -d /tmp/newsjacking-core-v3.4.0

cp -a /tmp/newsjacking-core-v3.4.0/newsjacking-core-v3.4.0-blue-star-brand/. /opt/newsjacking-core/
cp /root/newsjacking-core.env.v3.4.0.backup /opt/newsjacking-core/.env

cd /opt/newsjacking-core
docker compose build web worker beat
docker compose up -d --remove-orphans
```

Do not run `docker compose down -v`; that would remove persistent data volumes.

## Verify

```bash
cd /opt/newsjacking-core
docker compose ps
curl -fsS http://127.0.0.1:8010/health && echo
docker compose logs --tail=80 web
```

Expected health response includes:

```json
{"status":"ok","version":"3.4.0"}
```

Then open `https://njs.syntal.pro` and hard-refresh once so the new stylesheet and SVG favicon are loaded.
