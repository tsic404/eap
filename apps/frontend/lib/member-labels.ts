import type { BadgeVariant } from "@/components/ui/badge";

import { MEMBER_ROLES, type MemberRole } from "./member-types";

/** Message key for a role; an unknown server value falls back to itself. */
export function memberRoleKey(role: string): string {
  return MEMBER_ROLES.includes(role as MemberRole) ? `roles.${role}` : role;
}

/** Message key for a status; an unknown server value falls back to itself. */
export function memberStatusKey(status: string): string {
  return status === "active" || status === "disabled" ? `statuses.${status}` : status;
}

/** Message key for an account source; an unknown server value falls back to itself. */
export function accountSourceKey(source: string): string {
  return source === "local" || source === "sso" ? `sources.${source}` : source;
}

/** Badge colour carrying the same meaning as the status text (never colour-only). */
export function memberStatusVariant(status: string): BadgeVariant {
  if (status === "active") return "success";
  if (status === "disabled") return "danger";
  return "default";
}
