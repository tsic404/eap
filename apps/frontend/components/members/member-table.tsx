"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Column } from "@/components/ui/table";
import { DataTable } from "@/components/ui/table";
import {
  accountSourceKey,
  memberRoleKey,
  memberStatusKey,
  memberStatusVariant,
} from "@/lib/member-labels";
import type { Member, MemberStatus } from "@/lib/member-types";

export interface MemberTableProps {
  members: Member[];
  canResetPassword: boolean;
  /** Prompt shown for an empty roster; the closed capability gets a create-free one (C-110). */
  emptyDescription: string;
  busyMemberId: string | null;
  onChangeStatus: (member: Member, status: MemberStatus) => void;
  onResetPassword: (member: Member) => void;
}

/** Admin roster: source, forced-change state and the per-member actions (C-28/C-116). */
export function MemberTable({
  members,
  canResetPassword,
  emptyDescription,
  busyMemberId,
  onChangeStatus,
  onResetPassword,
}: MemberTableProps) {
  const t = useTranslations("members");

  const columns: Column<Member>[] = [
    {
      key: "member",
      header: t("columns.member"),
      cell: (row) => (
        <span className="flex flex-col">
          <span className="font-medium text-foreground">{row.name}</span>
          <span className="text-xs text-muted-foreground">{row.email}</span>
        </span>
      ),
    },
    {
      key: "role",
      header: t("columns.role"),
      cell: (row) => t(memberRoleKey(row.role)),
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (row) => (
        <Badge variant={memberStatusVariant(row.status)}>
          {t(memberStatusKey(row.status))}
        </Badge>
      ),
    },
    {
      key: "accountSource",
      header: t("columns.source"),
      cell: (row) => (
        <Badge variant={row.accountSource === "local" ? "info" : "default"}>
          {t(accountSourceKey(row.accountSource))}
        </Badge>
      ),
    },
    {
      key: "mustChangePassword",
      header: t("columns.mustChange"),
      cell: (row) => (
        <Badge variant={row.mustChangePassword ? "warning" : "default"}>
          {row.mustChangePassword ? t("mustChange.yes") : t("mustChange.no")}
        </Badge>
      ),
    },
    {
      key: "actions",
      header: t("columns.actions"),
      cell: (row) => (
        <div className="flex items-center gap-2">
          {row.status === "active" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={busyMemberId === row.id}
              onClick={() => onChangeStatus(row, "disabled")}
            >
              {t("disable")}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              disabled={busyMemberId === row.id}
              onClick={() => onChangeStatus(row, "active")}
            >
              {t("enable")}
            </Button>
          )}
          {canResetPassword && row.accountSource === "local" && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busyMemberId === row.id}
              onClick={() => onResetPassword(row)}
            >
              {t("reset")}
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      data={members}
      rowKey={(row) => row.id}
      pageSize={Math.max(members.length, 1)}
      showPagination={false}
      emptyTitle={t("empty.title")}
      emptyDescription={emptyDescription}
    />
  );
}
