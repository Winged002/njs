#!/usr/bin/env bash
set -euo pipefail

mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin <<MONGO
use ${MONGO_DB}
if (db.getUser("${MONGO_APP_USER}") === null) {
  db.createUser({
    user: "${MONGO_APP_USER}",
    pwd: "${MONGO_APP_PASSWORD}",
    roles: [{ role: "readWrite", db: "${MONGO_DB}" }]
  });
}
MONGO
