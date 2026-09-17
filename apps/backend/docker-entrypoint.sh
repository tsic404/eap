#!/bin/sh
set -eu

# Fresh databases get their business tables before the API starts serving;
# app.migrate serializes concurrent replicas with a Postgres advisory lock.
python -m app.migrate

exec "$@"
