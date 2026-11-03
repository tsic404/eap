"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { ROUTES } from "@/lib/api-routes";
import type { UserRole } from "@/lib/roles";

/** Admin landing page per role (C-15); employees have no admin entry. */
const ADMIN_LANDING_BY_ROLE: Partial<Record<UserRole, string>> = {
  platform_admin: ROUTES.adminHome,
  agent_admin: ROUTES.adminHome,
  knowledge_admin: ROUTES.adminKnowledge,
  auditor: ROUTES.adminRunLogs,
};

const ENTRY_CLASSES =
  "rounded-md px-2 py-1.5 text-sm font-medium text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export interface WorkspaceEntryProps {
  /** Current user's role; `employee` sees no entry in either area. */
  role: UserRole;
}

/**
 * The single top-bar entry between the user workspace and the admin area:
 * the destination follows the role, the label follows the area on screen.
 */
export function WorkspaceEntry({ role }: WorkspaceEntryProps) {
  const pathname = usePathname() ?? "";
  const t = useTranslations("workspace");
  const adminPath = ADMIN_LANDING_BY_ROLE[role];

  if (adminPath === undefined) return null;

  const isAdminArea =
    pathname === ROUTES.adminHome || pathname.startsWith(`${ROUTES.adminHome}/`);

  return (
    <Link href={isAdminArea ? ROUTES.home : adminPath} className={ENTRY_CLASSES}>
      {isAdminArea ? t("user") : t("admin")}
    </Link>
  );
}
