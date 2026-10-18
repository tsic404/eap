#!/usr/bin/env bash
# Bootstrap .env from .env.example and clear every template placeholder with a
# generated value, so no publicly known default ever reaches a running stack.
# Values for externally managed credentials (IdP, Dify console, signing keys)
# stay deliberately unusable: production supplies its own, see deploy/checklist.md.
# A value exported in the caller's shell still overrides .env at compose time,
# but the file is filled anyway so no template placeholder survives there.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
ENV_EXAMPLE="${ENV_EXAMPLE:-.env.example}"

# Placeholders replaced with a random secret. A value only counts as set after
# surrounding whitespace and one pair of quotes are stripped, so an empty or
# whitespace-only (even quoted) value is regenerated — as is a value still equal
# to the `.env.example` placeholder.
RANDOM_SECRETS=(
  WEAVIATE_API_KEY
  OIDC_CLIENT_ID
  OIDC_CLIENT_SECRET
  JWT_PRIVATE_KEY
  JWT_PUBLIC_KEY
  DIFY_SECRET_KEY
  DIFY_CONSOLE_PASSWORD
  PLUGIN_DAEMON_KEY
  PLUGIN_DIFY_INNER_API_KEY
)

normalize_value() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
    value="${value:1:$(( ${#value} - 2 ))}"
  elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
    value="${value:1:$(( ${#value} - 2 ))}"
  fi
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

# Normalized value of the last `var=` assignment in file; empty if absent/blank.
file_value() {
  local file="$1" var="$2" line value
  line="$(grep -E "^[[:space:]]*${var}[[:space:]]*=" "${file}" | tail -n 1)" || return 0
  value="${line#*=}"
  normalize_value "${value}"
}

# A value that is empty or still equal to the `.env.example` template is a
# placeholder the user has not replaced yet, not a real value — treat it as unset.
is_placeholder() {
  local var="$1" value="$2" template=""
  if [[ -r "${ENV_EXAMPLE}" ]]; then
    template="$(file_value "${ENV_EXAMPLE}" "${var}")"
  fi
  [[ -z "${value}" || "${value}" == "${template}" ]]
}

write_value() {
  local file="$1" var="$2" key="$3"
  if grep -qE "^[[:space:]]*${var}[[:space:]]*=" "${file}"; then
    sed -i.bak -E "s|^[[:space:]]*${var}[[:space:]]*=.*|${var}=${key}|" "${file}"
    rm -f "${file}.bak"
  else
    # Append only after guaranteeing a trailing newline, so the new line never
    # merges with a last line that lacked one.
    if [[ -s "${file}" && -n "$(tail -c 1 "${file}")" ]]; then
      printf '\n' >> "${file}"
    fi
    printf '%s=%s\n' "${var}" "${key}" >> "${file}"
  fi
}

# Replace one placeholder unless the file already holds a real value. A real
# shell export wins over .env at compose time but is not copied into the file;
# the file is still filled, because the placeholder contract judges the file. An
# empty or template-valued export would ship a known value, so fail fast.
fill_placeholder() {
  local var="$1" generator="$2"
  if [[ -n "${!var+x}" ]] && is_placeholder "${var}" "$(normalize_value "${!var}")"; then
    printf 'error: %s is exported empty or as the .env.example placeholder; docker compose uses it over .env.\n' "${var}" >&2
    printf '       run `unset %s` (or `env -u %s make setup-env`) and retry.\n' "${var}" "${var}" >&2
    exit 1
  fi
  is_placeholder "${var}" "$(file_value "${ENV_FILE}" "${var}")" || return 0
  write_value "${ENV_FILE}" "${var}" "$("${generator}")"
}

random_secret() { openssl rand -hex 32; }

# A random host under the reserved, non-resolving TLD keeps the value URL-shaped
# without naming an IdP that could ever answer.
random_issuer() { printf 'https://%s.invalid' "$(openssl rand -hex 16)"; }

random_console_email() { printf 'dify-console-%s@example.com' "$(openssl rand -hex 8)"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
fi

for var in "${RANDOM_SECRETS[@]}"; do
  fill_placeholder "${var}" random_secret
done

fill_placeholder OIDC_ISSUER random_issuer
fill_placeholder DIFY_CONSOLE_EMAIL random_console_email

echo ".env ready: template placeholders replaced where empty or left as shipped."
