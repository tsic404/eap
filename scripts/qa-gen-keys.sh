#!/usr/bin/env bash
# Generate the RS256 keypair the QA backend signs/verifies access tokens with.
# Idempotent: existing keys are kept, so tokens survive `make qa-down`/`qa-up`
# cycles. Output lives under deploy/qa/keys/ (gitignored) and is mounted into
# the backend container via JWT_*_KEY_FILE (see docker-compose.qa.yml).
set -euo pipefail

KEYS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy/qa/keys"
PRIVATE_KEY="${KEYS_DIR}/jwt_private.pem"
PUBLIC_KEY="${KEYS_DIR}/jwt_public.pem"

if [[ -s "${PRIVATE_KEY}" && -s "${PUBLIC_KEY}" ]]; then
  echo "QA JWT keys already present at deploy/qa/keys/; skipping."
  exit 0
fi

mkdir -p "${KEYS_DIR}"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${PRIVATE_KEY}"
openssl pkey -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}"
# 0644 rather than 0600: the backend container runs as a non-root user and reads
# these via a bind-mounted secret. They are throwaway QA keys (regenerated on
# demand, gitignored), so world-readable on the dev machine is acceptable.
chmod 644 "${PRIVATE_KEY}" "${PUBLIC_KEY}"
echo "Generated QA JWT keypair at deploy/qa/keys/."
