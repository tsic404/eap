"use client";

import { AdminForbidden } from "@/components/auth/admin-forbidden";
import { useRole } from "@/components/auth/role-context";
import { canManageMembers } from "@/lib/roles";

import { MemberManagement } from "./member-management";

/** `/admin/users` entry: the member surface is the platform administrator's alone (C-28). */
export function MemberListPage() {
  const role = useRole();
  if (!canManageMembers(role)) {
    return <AdminForbidden />;
  }
  return <MemberManagement />;
}
