/**
 * Reusable validation helpers. Prefer these over hand-rolled regex in forms so
 * validation rules stay consistent across the app and are covered by tests.
 */

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const URL_REGEX =
  /^(https?:\/\/)?([\w-]+\.)+[\w-]{2,}(:\d+)?(\/[^\s]*)?$/i;

/** Agent ID: lowercase word characters and hyphens, 3–64 chars. */
const AGENT_ID_REGEX = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_REGEX.test(value.trim());
}

export function isValidUrl(value: string): boolean {
  return URL_REGEX.test(value.trim());
}

export function isValidAgentId(value: string): boolean {
  return AGENT_ID_REGEX.test(value.trim());
}
