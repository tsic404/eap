-- Create the Dify database on first volume initialization.
-- The backend uses the default database (POSTGRES_DB), while Dify connects to
-- `dify` (see x-dify-env DB_DATABASE). Postgres only auto-creates the default
-- database, so the second one is provisioned here.
CREATE DATABASE dify;
