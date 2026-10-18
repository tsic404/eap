/**
 * Member-management API layer (C-28 / C-114 ~ C-116).
 *
 * Every function returns the unwrapped `data` payload; errors stay Axios errors
 * so callers can branch on the HTTP status plus the stable `error.code`.
 */

import axios from "axios";

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type {
  CreateMemberInput,
  Member,
  MemberListParams,
  MemberPage,
  MemberStatus,
} from "./member-types";

interface MemberErrorDetail {
  loc?: unknown[];
  msg?: string;
}

interface MemberErrorBody {
  error?: { code?: string; message?: string; details?: MemberErrorDetail[] };
}

function isMemberError(error: unknown, status: number, code: string): boolean {
  if (!axios.isAxiosError(error) || error.response?.status !== status) return false;
  const body = error.response.data as MemberErrorBody | undefined;
  return body?.error?.code === code;
}

/** One page of the tenant roster, filtered by name/email substring, role and status. */
export async function listMembers(params: MemberListParams): Promise<MemberPage> {
  const response = await apiClient.get<ApiEnvelope<MemberPage>>(API_ROUTES.members, {
    params,
  });
  return response.data.data;
}

/** Create a local account; it starts out needing a password change (C-114). */
export async function createMember(input: CreateMemberInput): Promise<Member> {
  const response = await apiClient.post<ApiEnvelope<Member>>(API_ROUTES.members, input);
  return response.data.data;
}

/** Switch a member between active and disabled (C-116). */
export async function setMemberStatus(
  memberId: string,
  status: MemberStatus,
): Promise<Member> {
  const response = await apiClient.patch<ApiEnvelope<Member>>(
    `${API_ROUTES.members}/${memberId}`,
    { status },
  );
  return response.data.data;
}

/**
 * Set a member's password to the administrator-provided value (C-115). The
 * response never carries the password back; the account is forced to change it
 * on the next login.
 */
export async function resetMemberPassword(
  memberId: string,
  password: string,
): Promise<Member> {
  const response = await apiClient.post<ApiEnvelope<Member>>(
    `${API_ROUTES.members}/${memberId}/password`,
    { password },
  );
  return response.data.data;
}

/** The address already belongs to an account of either source (409 `ACCOUNT_EXISTS`). */
export function isAccountExistsError(error: unknown): boolean {
  return isMemberError(error, 409, "ACCOUNT_EXISTS");
}

/** The email field failed the tenant-domain rule (422, C-114). */
export function isMemberEmailValidationError(error: unknown): boolean {
  if (!axios.isAxiosError(error) || error.response?.status !== 422) return false;
  const body = error.response.data as MemberErrorBody | undefined;
  return (body?.error?.details ?? []).some(
    (detail) => Array.isArray(detail.loc) && detail.loc.includes("email"),
  );
}

/**
 * The operation reads as absent (404 `NOT_FOUND`): the capability is off
 * (C-110), the reset target is not a local account, or the member is gone. The
 * backend keeps those three indistinguishable, so callers claim no cause.
 */
export function isUnavailableError(error: unknown): boolean {
  return isMemberError(error, 404, "NOT_FOUND");
}

/** The last platform administrator cannot be disabled (409 `LAST_ADMIN`). */
export function isLastAdminError(error: unknown): boolean {
  return isMemberError(error, 409, "LAST_ADMIN");
}

/** An account cannot disable itself (409 `SELF_DISABLE`). */
export function isSelfDisableError(error: unknown): boolean {
  return isMemberError(error, 409, "SELF_DISABLE");
}

/** The caller's role is not allowed on this surface (403 `FORBIDDEN`). */
export function isMemberForbiddenError(error: unknown): boolean {
  return isMemberError(error, 403, "FORBIDDEN");
}
