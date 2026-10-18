"use client";

import { useEffect, useState } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { fetchAuthCapabilities } from "@/lib/auth-service";
import {
  isLastAdminError,
  isMemberForbiddenError,
  isSelfDisableError,
  setMemberStatus,
} from "@/lib/member-service";
import { memberRoleKey } from "@/lib/member-labels";
import { MEMBER_ROLES, type Member, type MemberStatus } from "@/lib/member-types";
import { useMembers } from "@/lib/use-members";

import { CreateMemberDialog } from "./create-member-dialog";
import { MemberTable } from "./member-table";
import { ResetPasswordDialog } from "./reset-password-dialog";

const PAGE_SIZE = 20;

/** Platform administrator's roster: filters, local-account actions, status switches. */
export function MemberManagement() {
  const t = useTranslations("members");
  const { toast } = useToast();

  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<Member | null>(null);
  const [busyMemberId, setBusyMemberId] = useState<string | null>(null);
  const [isLocalLoginEnabled, setIsLocalLoginEnabled] = useState(false);

  // Creation and password reset are entries the closed capability must not show
  // (C-110); an unreachable read point leaves them hidden too.
  useEffect(() => {
    let cancelled = false;
    fetchAuthCapabilities()
      .then((capabilities) => {
        if (!cancelled) setIsLocalLoginEnabled(capabilities.localLoginEnabled);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const { data, error, isLoading, mutate } = useMembers({
    page,
    pageSize: PAGE_SIZE,
    search: search === "" ? undefined : search,
    role: roleFilter === "" ? undefined : roleFilter,
    status: statusFilter === "" ? undefined : statusFilter,
  });

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // A filter change can shrink the roster under the current page index.
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const handleSearch = () => {
    setPage(1);
    setSearch(searchDraft.trim());
  };

  const handleStatusChange = async (member: Member, status: MemberStatus) => {
    setBusyMemberId(member.id);
    try {
      await setMemberStatus(member.id, status);
      toast({
        type: "success",
        title: status === "disabled" ? t("disabled") : t("enabled"),
      });
      await mutate();
    } catch (statusError) {
      const title = isLastAdminError(statusError)
        ? t("errors.lastAdmin")
        : isSelfDisableError(statusError)
          ? t("errors.selfDisable")
          : isMemberForbiddenError(statusError)
            ? t("errors.forbidden")
            : t("errors.generic");
      toast({ type: "error", title });
    } finally {
      setBusyMemberId(null);
    }
  };

  const handleCreated = () => {
    setIsCreateOpen(false);
    toast({ type: "success", title: t("created") });
    void mutate();
  };

  const handleReset = () => {
    setResetTarget(null);
    toast({ type: "success", title: t("passwordReset") });
    void mutate();
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        {isLocalLoginEnabled && (
          <Button onClick={() => setIsCreateOpen(true)}>{t("create")}</Button>
        )}
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
        <form
          className="flex flex-1 items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            handleSearch();
          }}
        >
          <Input
            label={t("searchLabel")}
            placeholder={t("searchPlaceholder")}
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
          />
          <Button type="submit" variant="outline">
            {t("search")}
          </Button>
        </form>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="member-role-filter" className="text-sm font-medium text-foreground">
            {t("roleFilter")}
          </label>
          <select
            id="member-role-filter"
            value={roleFilter}
            onChange={(event) => {
              setPage(1);
              setRoleFilter(event.target.value);
            }}
            className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">{t("allRoles")}</option>
            {MEMBER_ROLES.map((role) => (
              <option key={role} value={role}>
                {t(memberRoleKey(role))}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="member-status-filter" className="text-sm font-medium text-foreground">
            {t("statusFilter")}
          </label>
          <select
            id="member-status-filter"
            value={statusFilter}
            onChange={(event) => {
              setPage(1);
              setStatusFilter(event.target.value);
            }}
            className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">{t("allStatuses")}</option>
            <option value="active">{t("statuses.active")}</option>
            <option value="disabled">{t("statuses.disabled")}</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : error ? (
        <ErrorBanner onRetry={() => void mutate()} />
      ) : (
        <>
          <MemberTable
            members={data?.items ?? []}
            canResetPassword={isLocalLoginEnabled}
            emptyDescription={
              isLocalLoginEnabled ? t("empty.description") : t("empty.descriptionClosed")
            }
            busyMemberId={busyMemberId}
            onChangeStatus={(member, status) => void handleStatusChange(member, status)}
            onResetPassword={setResetTarget}
          />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{t("pagination.total", { total })}</span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                {t("pagination.prev")}
              </Button>
              <span>{t("pagination.page", { page, pages: pageCount })}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount}
                onClick={() => setPage(page + 1)}
              >
                {t("pagination.next")}
              </Button>
            </div>
          </div>
        </>
      )}

      <CreateMemberDialog
        open={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onCreated={handleCreated}
      />
      <ResetPasswordDialog
        member={resetTarget}
        onClose={() => setResetTarget(null)}
        onReset={handleReset}
      />
    </div>
  );
}
