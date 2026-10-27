#!/usr/bin/env bash
# Generate the QA variant's stack secrets (C-91 / C-92: local family, never in
# the repository). Idempotent like gen-jwt-keys.sh: existing values are kept, so
# a stack restart reuses the Dify session key and the plugin daemon keys.
#
# The generated file is injected as the compose `--env-file` (Makefile, e2e
# runner): every `${VAR:?}` in docker-compose.qa.yml interpolates from it, so a
# missing file or key fails configuration instead of resolving to a placeholder.
#
# Usage: gen-qa-secrets.sh [output-file]   (default: deploy/qa/qa.env)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-deploy/qa/qa.env}"
if [[ "${ENV_FILE}" != /* ]]; then
  ENV_FILE="${REPO_ROOT}/${ENV_FILE}"
fi

random_secret() { openssl rand -hex 32; }
# Dify's setup validator: at least 8 characters, with a letter and a digit.
random_console_password() { printf 'Qa%s' "$(openssl rand -hex 16)"; }

# Last non-comment assignment of `key` in the file; empty when absent.
file_value() {
  local key="$1" line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${ENV_FILE}" 2>/dev/null | tail -n 1)" || true
  printf '%s' "${line#*=}"
}

write_value() {
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "${ENV_FILE}" 2>/dev/null; then
    sed -i.bak -E "s|^[[:space:]]*${key}[[:space:]]*=.*|${key}=${value}|" "${ENV_FILE}"
    rm -f "${ENV_FILE}.bak"
  else
    if [[ -s "${ENV_FILE}" && -n "$(tail -c 1 "${ENV_FILE}")" ]]; then
      printf '\n' >> "${ENV_FILE}"
    fi
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

# Keep `key` when set and non-empty; generate it otherwise.
ensure_value() {
  local key="$1" generator="$2"
  if [[ -n "$(file_value "${key}")" ]]; then
    return 0
  fi
  write_value "${key}" "$("${generator}")"
}

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

ensure_value DIFY_SECRET_KEY random_secret
ensure_value WEAVIATE_API_KEY random_secret
ensure_value PLUGIN_DAEMON_KEY random_secret
ensure_value PLUGIN_DIFY_INNER_API_KEY random_secret
ensure_value DIFY_CONSOLE_PASSWORD random_console_password
ensure_value QA_MODEL_API_KEY random_secret

if [[ -z "$(file_value DIFY_CONSOLE_EMAIL)" ]]; then
  write_value DIFY_CONSOLE_EMAIL qa-console@example.com
fi

echo "QA stack secrets ready at ${ENV_FILE}."
