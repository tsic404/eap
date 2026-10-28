#!/usr/bin/env bash
# Run a stack-seeded Playwright journey against the compose QA stack.
#
#   make e2e                                  # tc-auth-08 against the QA stack
#   make e2e E2E_SPEC=e2e/journeys/<journey>.spec.ts
#
# Scope: specs that need only the QA stack's seeded tenant/accounts and plain
# Playwright. The full suite still belongs to `playwright.config.mjs`, which
# starts four host processes and replays the DB/Redis setup; specs that depend
# on that global setup, or on app-only packages such as `@axe-core/playwright`,
# are out of scope here. See README «容器内 e2e 运行器».
#
# The host needs Docker (with the compose plugin) and a POSIX shell; pnpm, a
# glibc browser, PostgreSQL and the backend venv are all supplied by containers.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(docker compose --env-file deploy/qa/qa.env -f docker-compose.qa.yml)
IMAGE="${E2E_IMAGE:-eap-e2e:local}"
SPEC="${E2E_SPEC:-e2e/journeys/tc-auth-08-local-login.spec.ts}"
PROXY_PORT="${QA_PROXY_PORT:-8090}"
BASE_URL="${E2E_BASE_URL:-http://localhost:${PROXY_PORT}}"
ARTIFACTS="${E2E_ARTIFACTS:-artifacts/playwright}"
STACK_TIMEOUT="${E2E_STACK_TIMEOUT:-1800}"
# The value the QA stack must carry again once this script exits. The login
# bucket is a contract value (10/min); the run overrides it below and restores
# it on the way out so later evidence collected on the stack sees the contract.
RESTORE_LOGIN_RATE="${RATE_LIMIT_LOGIN_PER_MINUTE:-10}"
export RATE_LIMIT_LOGIN_PER_MINUTE="${E2E_LOGIN_RATE_LIMIT:-300}"

# Resident services of the local-family shape (C-88), minus none: every one of
# them declares a healthcheck, the reverse proxy included.
RESIDENT_SERVICES=(
  postgres redis weaviate mock-idp
  dify-api dify-worker plugin-daemon backend frontend reverse-proxy
)

# Keep scratch files beside the artifacts instead of the host's /tmp: docker
# compose materialises build metadata in TMPDIR, and constrained hosts (small
# tmpfs, per-user quota) otherwise fail the bring-up after a successful build.
[[ "${ARTIFACTS}" = /* ]] || ARTIFACTS="${REPO_ROOT}/${ARTIFACTS}"
export TMPDIR="${E2E_TMPDIR:-${ARTIFACTS}/.tmp}"
mkdir -p "${ARTIFACTS}" "${TMPDIR}"

command -v docker >/dev/null 2>&1 || {
  echo "e2e: docker is required" >&2
  exit 1
}

STACK_STARTED=0
STACK_UP_COMPLETE=0
restore_login_throttle() {
  local status=$?
  trap - EXIT
  if [[ "${STACK_STARTED}" == "1" ]]; then
    # The contract covers any exit after a successful bring-up. A bring-up that
    # failed is restored only when a backend existed before it: that stack would
    # otherwise be left on the raised value, while a cold start has nothing to
    # protect and a lone backend without its dependencies is worse than none.
    if [[ "${STACK_UP_COMPLETE}" == "1" || -n "${PRE_BACKEND_ID}" ]]; then
      echo "e2e: restoring the QA login throttle to ${RESTORE_LOGIN_RATE}/min ..."
      export RATE_LIMIT_LOGIN_PER_MINUTE="${RESTORE_LOGIN_RATE}"
      # The recreate waits for healthy now, and the backend's own health gate
      # allows a 300s start window; a shorter timeout would fail a finished run
      # on a loaded host.
      if "${COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 360 backend >/dev/null 2>&1; then
        local backend_id value
        backend_id="$("${COMPOSE[@]}" ps -q backend 2>/dev/null || true)"
        value="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${backend_id}" 2>/dev/null \
          | sed -n 's/^RATE_LIMIT_LOGIN_PER_MINUTE=//p')"
        if [[ "${value}" == "${RESTORE_LOGIN_RATE}" ]]; then
          echo "e2e: QA login throttle restored to ${value}/min"
        else
          echo "e2e: QA login throttle is '${value}', expected '${RESTORE_LOGIN_RATE}'" >&2
          status=1
        fi
      else
        echo "e2e: failed to recreate the QA backend with the contract login throttle" >&2
        status=1
      fi
    else
      echo "e2e: stack start failed before any backend existed; skipping the login throttle restore" >&2
    fi
  fi
  exit "${status}"
}
trap restore_login_throttle EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 1. Bring the QA stack up (idempotent) and wait for the readiness baseline:
# every resident service with a healthcheck reports healthy, the one-shot
# bootstrap exits 0, and the entry point answers through the reverse proxy.
./scripts/gen-jwt-keys.sh deploy/qa/keys >/dev/null
./scripts/gen-qa-secrets.sh >/dev/null
# A partial bring-up may already carry the raised value, so the restore window
# opens before `up`; whether restoring a failed bring-up is worthwhile depends
# on a backend having existed beforehand (see the trap).
PRE_BACKEND_ID="$("${COMPOSE[@]}" ps -a -q backend 2>/dev/null || true)"
STACK_STARTED=1
"${COMPOSE[@]}" up -d --build
# The stub IdP container also serves the tool upstream the approval journeys
# point at, and its source is a bind-mounted file Node reads once at start. A
# stack that outlives an edit to that file would keep answering without the new
# routes, so the stub is recreated explicitly on every run.
"${COMPOSE[@]}" up -d --no-deps --force-recreate mock-idp
STACK_UP_COMPLETE=1

stack_ready() {
  local service container health
  for service in "${RESIDENT_SERVICES[@]}"; do
    container="$("${COMPOSE[@]}" ps -a -q "${service}" 2>/dev/null || true)"
    [[ -n "${container}" ]] || return 1
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || true)"
    [[ "${health}" == "healthy" ]] || return 1
  done
  return 0
}

echo "e2e: waiting for the QA stack (resident health + bootstrap seed + entry) ..."
deadline=$((SECONDS + STACK_TIMEOUT))
while ! stack_ready; do
  if ((SECONDS > deadline)); then
    echo "e2e: resident services were not healthy within ${STACK_TIMEOUT}s" >&2
    "${COMPOSE[@]}" ps -a >&2
    exit 1
  fi
  sleep 2
done

while :; do
  # `-a`: the bootstrap container has already stopped by the time it matters,
  # and plain `ps` would hide it and wait forever.
  seed_container="$("${COMPOSE[@]}" ps -a -q db-seed 2>/dev/null || true)"
  seed_state=""
  if [[ -n "${seed_container}" ]]; then
    seed_state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "${seed_container}" 2>/dev/null || true)"
  fi
  case "${seed_state}" in
    "exited 0") break ;;
    "exited "*)
      echo "e2e: db-seed failed (${seed_state})" >&2
      "${COMPOSE[@]}" logs db-seed >&2
      exit 1
      ;;
  esac
  if ((SECONDS > deadline)); then
    echo "e2e: the QA stack did not finish bootstrapping within ${STACK_TIMEOUT}s" >&2
    "${COMPOSE[@]}" ps -a >&2
    exit 1
  fi
  sleep 2
done

while :; do
  if "${COMPOSE[@]}" exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://reverse-proxy:${PROXY_PORT}/api/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  if ((SECONDS > deadline)); then
    echo "e2e: the entry point ${BASE_URL}/api/health never became healthy" >&2
    "${COMPOSE[@]}" ps -a >&2
    exit 1
  fi
  sleep 2
done

# 2. Drop the previous run's login state. The QA stack's Redis is long-lived,
# unlike the CI harness's throwaway instance, so a lockout or bucket left by an
# earlier run would leak into this one — the lockout journey notices first.
# Only login keys are cleared; sessions and every other key stay.
"${COMPOSE[@]}" exec -T redis sh -c '
  for pattern in "login:*" "ratelimit:login:*"; do
    keys=$(redis-cli --scan --pattern "$pattern")
    [ -z "$keys" ] || redis-cli DEL $keys >/dev/null
  done
'

# 3. Seed the local accounts the journeys sign in with. Runs inside the backend
# container so the application's own password hash and schema apply.
"${COMPOSE[@]}" exec -T backend python - < deploy/e2e/seed_accounts.py

# 4. Seed the data the requested journey asserts on and hand the runner its
# manifest. The spec name scopes the seed: an unrelated journey seeds nothing,
# so the long-lived stack keeps its accumulated state. The seed prints the
# manifest on stdout and its log lines on stderr, so the redirect keeps the
# file parseable.
"${COMPOSE[@]}" exec -T \
  -e "MOCK_IDP_PORT=${QA_IDP_PORT:-4000}" \
  -e "E2E_FIXTURE_SPEC=${SPEC}" \
  backend python - < deploy/e2e/seed_fixtures.py > "${ARTIFACTS}/fixtures.json"

# 5. Runner image: the Playwright version comes from the repo lockfile and the
# image installs its own locked dependency graph.
PLAYWRIGHT_VERSION="$(sed -n "s/^  '@playwright\/test@\([^']*\)':$/\1/p" pnpm-lock.yaml | sed -n '1p')"
if [[ -z "${PLAYWRIGHT_VERSION}" ]]; then
  echo "e2e: cannot resolve @playwright/test from pnpm-lock.yaml" >&2
  exit 1
fi
echo "e2e: Playwright ${PLAYWRIGHT_VERSION} (from pnpm-lock.yaml)"
docker build \
  --build-arg "PLAYWRIGHT_VERSION=${PLAYWRIGHT_VERSION}" \
  --tag "${IMAGE}" deploy/e2e

# 6. Drive the running stack from the runner container. Reports land on the
# host through /out; the checkout itself is mounted read-only.
status=0
docker run --rm \
  --network "${E2E_DOCKER_NETWORK:-host}" \
  --ipc=host \
  --env "E2E_BASE_URL=${BASE_URL}" \
  --env "E2E_REPORT_DIR=/out/report" \
  --env "E2E_OUTPUT_DIR=/out/test-results" \
  --env "E2E_FIXTURE_MANIFEST=/out/fixtures.json" \
  --volume "${REPO_ROOT}/apps/frontend:/src:ro" \
  --volume "${ARTIFACTS}:/out" \
  "${IMAGE}" \
  /opt/e2e/node_modules/.bin/playwright test \
  --config playwright.stack.config.mjs "${SPEC}" || status=$?

echo "e2e: spec=${SPEC} exit=${status}"
echo "e2e: report=${ARTIFACTS}/report/index.html"
exit "${status}"
