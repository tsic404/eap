-- Create the two databases Dify needs, idempotently.
--
-- Postgres only auto-creates POSTGRES_DB (`eap`, used by the backend). Dify's
-- api/worker connect to `dify` (x-dify-env DB_DATABASE) and the plugin daemon
-- to `dify_plugin` (plugin-daemon service DB_DATABASE). This file is executed
-- by the one-shot `dify-db-init` service on every `docker compose up`, so it
-- also provisions them on a volume that was initialized before the plugin
-- daemon existed (where the old docker-entrypoint-initdb.d script could not
-- run again).
--
-- `\gexec` runs each statement the SELECT produces, so an existing database is
-- skipped instead of erroring.
SELECT 'CREATE DATABASE dify'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dify')\gexec

SELECT 'CREATE DATABASE dify_plugin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dify_plugin')\gexec
