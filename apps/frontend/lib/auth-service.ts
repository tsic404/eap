/**
 * Auth capability read point and local-account login (C-110 / C-111).
 *
 * The login page renders its account/password form from the capability read
 * point alone, so a deployment that never declared the capability shows the
 * enterprise entry only. A successful login carries the refresh cookie and
 * nothing else — the access token still comes from the refresh channel.
 */

import { API_ROUTES } from "./api-routes";
import { PASSWORD_CHANGE_REQUIRED_CODE, type ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import { extractApiErrorCode } from "./platform-service";

/** Public capability read point; the backend answers in both states (C-110). */
export interface AuthCapabilities {
  localLoginEnabled: boolean;
}

/** What a successful local login reports besides the refresh cookie (C-111). */
export interface LocalLoginResult {
  mustChangePassword: boolean;
}

/** Rejection code (C-111) → copy key under the `login` message namespace. */
const LOGIN_ERROR_KEYS: Record<string, string> = {
  RATE_LIMITED: "errors.rateLimited",
  LOGIN_LOCKED: "errors.locked",
  INVALID_CREDENTIALS: "errors.invalidCredentials",
  TENANT_NOT_FOUND: "errors.tenantNotFound",
  TENANT_AMBIGUOUS: "errors.tenantAmbiguous",
  VALIDATION_ERROR: "errors.validation",
  NOT_FOUND: "errors.capabilityClosed",
};

const GENERIC_ERROR_KEY = "errors.generic";

/** Whether the deployment serves local account login. */
export async function fetchAuthCapabilities(): Promise<AuthCapabilities> {
  const response = await apiClient.get<ApiEnvelope<AuthCapabilities>>(API_ROUTES.capabilities);
  return { localLoginEnabled: response.data.data?.localLoginEnabled === true };
}

/** Authenticate a local account; the refresh cookie arrives on this response. */
export async function loginWithLocalAccount(
  email: string,
  password: string,
): Promise<LocalLoginResult> {
  const response = await apiClient.post<ApiEnvelope<LocalLoginResult>>(API_ROUTES.localLogin, {
    email,
    password,
  });
  return { mustChangePassword: response.data.data?.mustChangePassword === true };
}

/** Copy key for a rejected local login, so the form can render a readable reason. */
export function localLoginErrorKey(error: unknown): string {
  const code = extractApiErrorCode(error);
  return (code ? LOGIN_ERROR_KEYS[code] : undefined) ?? GENERIC_ERROR_KEY;
}

/** Contract bounds for a new password (C-113). */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

/** Rejection code (C-113) → copy key under the `password` message namespace. */
const PASSWORD_CHANGE_ERROR_KEYS: Record<string, string> = {
  INVALID_CREDENTIALS: "errors.invalidCurrent",
  VALIDATION_ERROR: "errors.validation",
};

const PASSWORD_CHANGE_GENERIC_ERROR_KEY = "errors.generic";

/**
 * Replace the signed-in account's own password (C-113).
 *
 * Success clears the forced-change flag and ends every refresh-token family the
 * account holds; access tokens already issued stay valid until they expire, so
 * the caller drops its local session and signs in again.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiClient.post(API_ROUTES.changePassword, { currentPassword, newPassword });
}

/** Copy key for a rejected change, so the form can render a readable reason. */
export function passwordChangeErrorKey(error: unknown): string {
  const code = extractApiErrorCode(error);
  return (code ? PASSWORD_CHANGE_ERROR_KEYS[code] : undefined) ?? PASSWORD_CHANGE_GENERIC_ERROR_KEY;
}

/** Whether a rejection is the forced-change gate (C-113). */
export function isPasswordChangeRequiredError(error: unknown): boolean {
  return extractApiErrorCode(error) === PASSWORD_CHANGE_REQUIRED_CODE;
}
