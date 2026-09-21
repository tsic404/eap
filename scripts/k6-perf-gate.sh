#!/usr/bin/env bash
# Fixed-resource k6 baseline gate (see .github/workflows/ci.yml `k6` job).
#
# The shared dev host is too noisy to gate reproducibly: other agents' jobs
# spike it to load 16+ and shift the whole latency distribution, so the
# authoritative run lives on a dedicated CI runner. This script migrates + seeds
# a disposable DB, boots the backend, mints an access token, and runs
# k6/agent-list.js — the exact steps the CI job performs. Local runs are
# tuning-only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT/apps/backend"
K6_SCRIPT="$ROOT/k6/agent-list.js"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://eap:eap_password@localhost:5432/eap}"
BASE_URL="${BASE_URL:-http://127.0.0.1:3001/api}"
PORT="${PORT:-3001}"

WORKDIR="$(mktemp -d)"
trap 'kill "${BACKEND_PID:-}" 2>/dev/null || true; rm -rf "$WORKDIR"' EXIT

cd "$BACKEND_DIR"

# 1. Schema + seed (asyncpg — no psql dependency on the runner).
uv run alembic upgrade head
uv run python - "$WORKDIR/ids" <<'PY'
import asyncio
import os
import sys
import uuid

async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    # Unique slug/agent ids so re-runs on the same DB (local tuning) don't collide.
    suffix = uuid.uuid4().hex[:8]
    await conn.execute(
        "INSERT INTO tenants (id, name, slug, sso_domain, sso_provider, quota_limit, quota_used, status) "
        "VALUES ($1, 'Perf', $2, 'perf.local', 'oidc', 10000, 0, 'active')",
        tenant_id,
        f"perf-{suffix}",
    )
    await conn.execute(
        "INSERT INTO users (id, tenant_id, sso_sub, email, name, role, status) "
        "VALUES ($1, $2, 'perf-user', 'perf@perf.local', 'Perf User', 'agent_admin', 'active')",
        user_id,
        tenant_id,
    )
    for i in range(1, 6):
        await conn.execute(
            "INSERT INTO agent_registry (agent_id, tenant_id, dify_app_id, name, description, type, category, icon, tags, status, version, created_by, published_at, published_by) "
            "VALUES ($1, $2, $3, $4, 'seed', 'chat', 'general', '🤖', '[\"perf\"]'::jsonb, 'published', 1, $5, now(), $5)",
            f"perf-agent-{suffix}-{i}",
            tenant_id,
            f"perf-dify-app-{suffix}-{i}",
            f"Perf Agent {i}",
            user_id,
        )
    await conn.close()
    with open(sys.argv[1], "w") as f:
        f.write(f"{user_id}\n{tenant_id}\n")


asyncio.run(main())
PY

USER_ID="$(sed -n '1p' "$WORKDIR/ids")"
TENANT_ID="$(sed -n '2p' "$WORKDIR/ids")"

# 2. JWT keypair + backend process.
openssl genrsa -out "$WORKDIR/priv.pem" 2048 2>/dev/null
openssl rsa -in "$WORKDIR/priv.pem" -pubout -out "$WORKDIR/pub.pem" 2>/dev/null

JWT_PRIVATE_KEY_FILE="$WORKDIR/priv.pem" JWT_PUBLIC_KEY_FILE="$WORKDIR/pub.pem" \
  "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" &
BACKEND_PID=$!

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/api/health/live" >/dev/null && break
  sleep 1
done

# 3. Access token signed with the same keypair the backend verifies against.
ACCESS_TOKEN="$("$BACKEND_DIR/.venv/bin/python" - "$WORKDIR/priv.pem" "$USER_ID" "$TENANT_ID" <<'PY'
import datetime
import sys

import jwt

now = datetime.datetime.now(datetime.timezone.utc)
payload = {
    "sub": sys.argv[2],
    "tenantId": sys.argv[3],
    "role": "agent_admin",
    "jti": "perf-gate",
    "iat": now,
    "exp": now + datetime.timedelta(seconds=900),
}
print(jwt.encode(payload, open(sys.argv[1]).read(), algorithm="RS256"))
PY
)"

# Fail fast with a clear message if the token/backend wiring is wrong.
curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" "$BASE_URL/agents" >/dev/null || {
  echo "perf-gate: token rejected by backend (auth wiring broken)" >&2
  exit 1
}

# 4. Run the baseline; k6 exits non-zero when the threshold is crossed.
k6 run "$K6_SCRIPT" -e ACCESS_TOKEN="$ACCESS_TOKEN" -e BASE_URL="$BASE_URL"
