#!/usr/bin/env bash
# Bootstrap .env from .env.example and fill required secrets that are still empty.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
ENV_EXAMPLE="${ENV_EXAMPLE:-.env.example}"

# Secrets docker compose enforces via `${VAR:?...}`. A value only counts as set
# after surrounding whitespace and one pair of quotes are stripped, so an empty
# or whitespace-only (even quoted) value is regenerated.
REQUIRED_SECRETS=(WEAVIATE_API_KEY DIFY_SECRET_KEY PLUGIN_DAEMON_KEY PLUGIN_DIFY_INNER_API_KEY)

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

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
fi

for var in "${REQUIRED_SECRETS[@]}"; do
  # A shell-exported value overrides .env in docker compose; an empty export
  # still trips `${VAR:?}` after .env is filled, so fail fast instead of
  # reporting a success that will not stick.
  if [[ -n "${!var+x}" ]]; then
    if [[ -z "$(normalize_value "${!var}")" ]]; then
      printf 'error: %s is exported but empty; docker compose uses it over .env.\n' "${var}" >&2
      printf '       run `unset %s` (or `env -u %s make setup-env`) and retry.\n' "${var}" "${var}" >&2
      exit 1
    fi
    continue
  fi

  [[ -n "$(file_value "${ENV_FILE}" "${var}")" ]] && continue
  write_value "${ENV_FILE}" "${var}" "$(openssl rand -hex 32)"
done

echo ".env ready: required secrets generated where empty."
