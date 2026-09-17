// SSO end-to-end regression: one-time setup.
//
// Resets and migrates the database, then seeds the tenant the mock IdP's user
// (alice@acme.com) maps onto via sso_domain. The backend resolves a user's
// tenant by email domain, so this tenant must exist before the browser flow
// runs.
//
// Safety: a destructive `DROP SCHEMA public CASCADE` is only ever run against
// a database whose name declares it disposable (suffix `_e2e`/`_test`), or
// when the operator explicitly opts in with `E2E_DESTRUCTIVE=1`. The default
// DSN therefore points at `eap_e2e`, never the development database `eap`.
//
// The reset is deliberate: the backend pytest fixtures drop all tables but
// leave `alembic_version` stamped at head, so a bare `alembic upgrade head`
// would no-op against a schema that no longer exists. Dropping and recreating
// the public schema (the initial migration recreates the pgvector/pgcrypto
// extensions) makes the run deterministic regardless of prior state.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(dirname, "../../..");
const backendDir = path.join(repoRoot, "apps/backend");

const PSQL_DSN =
  process.env.E2E_PSQL_DSN ?? "postgresql://eap:eap_password@localhost:5432/eap_e2e";
const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+asyncpg://eap:eap_password@localhost:5432/eap_e2e";
// Prefer the project venv; fall back to `python` on PATH (CI installs system-wide).
const venvPython = path.join(backendDir, ".venv/bin/python");
const BACKEND_PYTHON =
  process.env.E2E_BACKEND_PYTHON ?? (existsSync(venvPython) ? venvPython : "python");

// Only these databases may be destroyed without an explicit opt-in.
const DISPOSABLE_DB_RE = /_(?:e2e|test)$/;

const SEED_SQL = `
  INSERT INTO tenants (id, name, slug, sso_domain, sso_provider, quota_limit, quota_used, status)
  SELECT gen_random_uuid(), 'Acme', 'acme-e2e', 'acme.com', 'oidc', 10000, 0, 'active'
  WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE sso_domain = 'acme.com');
`;

function psqlUrl(dsn) {
  // asyncpg DSNs are URL-shaped too; normalise the scheme so URL can parse it.
  return new URL(dsn.replace(/^postgresql\+asyncpg:/, "postgresql:"));
}

function databaseName(dsn) {
  return psqlUrl(dsn).pathname.replace(/^\//, "");
}

function runPsql(dsn, args) {
  execFileSync("psql", [psqlUrl(dsn).toString(), ...args], { stdio: "inherit" });
}

export default async function globalSetup() {
  const dbName = databaseName(PSQL_DSN);
  const destructive = process.env.E2E_DESTRUCTIVE === "1";

  if (!destructive && !DISPOSABLE_DB_RE.test(dbName)) {
    console.error(
      `Refusing to reset database "${dbName}": its name does not look disposable. ` +
        "Use a database name ending in `_e2e` or `_test`, or set E2E_DESTRUCTIVE=1 " +
        "to explicitly opt into destroying this database.",
    );
    process.exit(1);
  }

  // Create the database if it does not exist yet, via the postgres admin DB.
  const adminUrl = psqlUrl(PSQL_DSN);
  adminUrl.pathname = "/postgres";
  const exists = execFileSync(
    "psql",
    [adminUrl.toString(), "-tAc", `SELECT 1 FROM pg_database WHERE datname = '${dbName}'`],
    { encoding: "utf8" },
  );
  if (!exists.trim()) {
    execFileSync("psql", [adminUrl.toString(), "-c", `CREATE DATABASE "${dbName}"`], {
      stdio: "inherit",
    });
  }

  // Deterministic reset from a known-clean schema.
  runPsql(PSQL_DSN, ["-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"]);

  execFileSync(BACKEND_PYTHON, ["-m", "alembic", "upgrade", "head"], {
    cwd: backendDir,
    env: { ...process.env, DATABASE_URL },
  });

  runPsql(PSQL_DSN, ["-c", SEED_SQL]);
}
