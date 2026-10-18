/**
 * Member-management domain types, mirroring the backend roster DTO (C-28).
 *
 * `role`, `status` and `accountSource` are kept as strings on the wire so an
 * unknown server value still renders instead of disappearing.
 */

import { USER_ROLES, type UserRole } from "./roles";

/** Roster roles: the platform's one role list (`lib/roles.ts`), shared not copied. */
export const MEMBER_ROLES = USER_ROLES;

export type MemberRole = UserRole;

export const MEMBER_STATUSES = ["active", "disabled"] as const;

export type MemberStatus = (typeof MEMBER_STATUSES)[number];

export const ACCOUNT_SOURCES = ["local", "sso"] as const;

export type AccountSource = (typeof ACCOUNT_SOURCES)[number];

/** One roster row as the backend exposes it (C-28). */
export interface Member {
  id: string;
  email: string;
  name: string;
  role: string;
  status: string;
  department: string | null;
  avatarText: string | null;
  lastActiveAt: string | null;
  createdAt: string;
  accountSource: string;
  mustChangePassword: boolean;
}

/** Page-number roster response: this page's entries plus the tenant's total (C-6). */
export interface MemberPage {
  items: Member[];
  total: number;
}

/** Query accepted by `GET /api/users`. */
export interface MemberListParams {
  page?: number;
  pageSize?: number;
  search?: string;
  role?: string;
  status?: string;
}

/** Body of a local-account creation (C-114). */
export interface CreateMemberInput {
  email: string;
  name: string;
  password: string;
}
