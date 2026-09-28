#!/usr/bin/env bash
# Generate the RS256 keypair the backend signs/verifies access tokens with.
# Idempotent: existing keys are kept, so tokens survive compose down/up cycles.
#
# Usage: gen-jwt-keys.sh [output-dir]   (default: deploy/keys)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEYS_DIR="${1:-deploy/keys}"
# A relative path means "relative to the repo root", not the caller's cwd.
[[ "${KEYS_DIR}" = /* ]] || KEYS_DIR="${REPO_ROOT}/${KEYS_DIR}"

PRIVATE_KEY="${KEYS_DIR}/jwt_private.pem"
PUBLIC_KEY="${KEYS_DIR}/jwt_public.pem"

if [[ -s "${PRIVATE_KEY}" && -s "${PUBLIC_KEY}" ]]; then
  echo "JWT keys already present at ${KEYS_DIR}; skipping."
  exit 0
fi

mkdir -p "${KEYS_DIR}"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${PRIVATE_KEY}"
openssl pkey -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}"
# 0644 rather than 0600: the backend container runs as a non-root user and reads
# these via a bind-mounted secret. They are throwaway development keys
# (regenerated on demand, gitignored), so world-readable on the dev machine is
# acceptable.
chmod 644 "${PRIVATE_KEY}" "${PUBLIC_KEY}"
echo "Generated JWT keypair at ${KEYS_DIR}."
