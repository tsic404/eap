/** Platform's five-tier role system (architecture v8.0 §1.4). */
export const USER_ROLES = [
  "platform_admin",
  "agent_admin",
  "knowledge_admin",
  "auditor",
  "employee",
] as const;

export type UserRole = (typeof USER_ROLES)[number];

/** Role applied when no authenticated session is established (least privilege). */
export const DEFAULT_ROLE: UserRole = "employee";

/** Cookie carrying the current user's role until OIDC/JWT auth is wired in. */
export const ROLE_COOKIE = "eap_role";

/** Type guard for untrusted role values (cookie/header/query). */
export function isUserRole(value: unknown): value is UserRole {
  return typeof value === "string" && (USER_ROLES as readonly string[]).includes(value);
}

/** Coerce an untrusted value to a valid role, defaulting to the least privilege. */
export function resolveRole(value: unknown): UserRole {
  return isUserRole(value) ? value : DEFAULT_ROLE;
}

/**
 * Whether a role may enter the admin workspace (`/admin`).
 *
 * Per the RBAC matrix (architecture v8.0 §8.2) only `employee` is denied
 * access to `/admin`; every other role (including read-only `auditor`) may
 * enter.
 */
export function canAccessAdmin(role: UserRole): boolean {
  return role !== "employee";
}
