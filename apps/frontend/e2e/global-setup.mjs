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
import { existsSync, unlinkSync, writeFileSync } from "node:fs";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
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

// The harness owns a throwaway Redis container on a dedicated port so the SSO
// flow never depends on the host's shared Redis, which can be in MISCONF (RDB
// save failure) and reject every write. Persistence is disabled (`--save ""`)
// so the container can never enter that state. An explicit E2E_REDIS_URL means
// the caller provides Redis and self-management is skipped. The port must match
// the backend's REDIS_URL default in playwright.config.mjs.
//
// Ownership: setup records the container id it created in a marker file;
// teardown removes only that container, so a reused/caller-provided Redis is
// never touched. setup runs after the DB reset so a failed setup step can never
// leak a container (Playwright skips teardown when globalSetup fails).
//
// The container name carries the setup process's pid, so no fixed name exists
// that one run could ever force-delete from another. A stale container left by
// a crashed run still holds the port and is reported, not silently killed.
const REDIS_PORT = process.env.E2E_REDIS_PORT ?? "6380";
const REDIS_CONTAINER = `eap-e2e-redis-${process.pid}`;
const REDIS_IMAGE = "redis:7-alpine";
const REDIS_MARKER = path.join(tmpdir(), "eap-e2e-redis-owner");

function redisPortInUse() {
  return new Promise((resolve) => {
    const socket = createConnection({ host: "127.0.0.1", port: Number(REDIS_PORT) });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

function redisContainerReady() {
  try {
    execFileSync("docker", ["exec", REDIS_CONTAINER, "redis-cli", "ping"], {
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

function removeRedisContainer(id) {
  try {
    execFileSync("docker", ["rm", "-f", id], { stdio: "ignore" });
  } catch {
    // Already gone (or Docker unavailable).
  }
}

function writeRedisMarker(containerId) {
  writeFileSync(REDIS_MARKER, containerId, "utf8");
}

function clearRedisMarker() {
  try {
    unlinkSync(REDIS_MARKER);
  } catch {
    // Marker already removed.
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureRedis() {
  if (process.env.E2E_REDIS_URL) {
    console.log("Redis is caller-provided (E2E_REDIS_URL set); skipping self-management.");
    return;
  }

  // No pre-flight delete: the port is only ever claimed by a container this run
  // created (unique name). If something else holds it — e.g. a stale
  // eap-e2e-redis-* container from a crashed run — fail with a hint instead of
  // force-killing a listener we do not own.
  if (await redisPortInUse()) {
    throw new Error(
      `127.0.0.1:${REDIS_PORT} is already in use. Remove any stale ` +
        "`eap-e2e-redis-*` container (`docker ps -a --filter name=eap-e2e-redis`) or set " +
        "E2E_REDIS_URL to a dedicated Redis.",
    );
  }

  const containerId = execFileSync(
    "docker",
    [
      "run",
      "-d",
      "--rm",
      "--name",
      REDIS_CONTAINER,
      "-p",
      `127.0.0.1:${REDIS_PORT}:6379`,
      REDIS_IMAGE,
      "redis-server",
      "--save",
      "",
      "--appendonly",
      "no",
    ],
    { encoding: "utf8" },
  ).trim();
  writeRedisMarker(containerId);

  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (redisContainerReady()) {
      console.log(`e2e Redis ready on 127.0.0.1:${REDIS_PORT} (${REDIS_CONTAINER}).`);
      return;
    }
    await sleep(250);
  }
  // Readiness never came up; clean up now. globalTeardown does not run when
  // globalSetup fails, so leaving the container here would leak it on the port.
  removeRedisContainer(containerId);
  clearRedisMarker();
  throw new Error(`e2e Redis container ${REDIS_CONTAINER} did not become ready within 30s`);
}

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

  // Start the Redis container only after the DB reset succeeds: any earlier
  // failure exits globalSetup, which skips teardown, so a container started
  // earlier would leak on the port.
  await ensureRedis();
}
