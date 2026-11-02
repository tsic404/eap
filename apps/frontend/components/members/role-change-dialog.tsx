"use client";

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import {
  isLastAdminError,
  isMemberForbiddenError,
  isMemberValidationError,
  isUnavailableError,
  setMemberRole,
} from "@/lib/member-service";
import { memberRoleKey } from "@/lib/member-labels";
import { MEMBER_ROLES, type Member, type MemberRole } from "@/lib/member-types";

export interface RoleChangeDialogProps {
  member: Member | null;
  onClose: () => void;
  onChanged: (member: Member) => void;
}

/**
 * Adjusts one member's role within the five-role ladder (C-28). The backend
 * owns the C-29 invariants, so a refusal is echoed by its `error.code`.
 */
export function RoleChangeDialog({ member, onClose, onChanged }: RoleChangeDialogProps) {
  const t = useTranslations("members");
  const formId = useId();
  const selectId = useId();
  const [role, setRole] = useState<MemberRole>("employee");
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (member === null) return;
    setRole(member.role as MemberRole);
    setFormError(null);
  }, [member]);

  const handleClose = () => {
    if (isSubmitting) return;
    onClose();
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || member === null) return;

    setFormError(null);
    setIsSubmitting(true);
    try {
      const updated = await setMemberRole(member.id, role);
      onChanged(updated);
    } catch (error) {
      if (isLastAdminError(error)) {
        setFormError(t("errors.lastAdminRole"));
      } else if (isMemberForbiddenError(error)) {
        setFormError(t("errors.forbidden"));
      } else if (isUnavailableError(error)) {
        setFormError(t("errors.unavailable"));
      } else if (isMemberValidationError(error)) {
        setFormError(t("errors.roleInvalid"));
      } else {
        setFormError(t("errors.generic"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={member !== null}
      onClose={handleClose}
      title={t("roleTitle")}
      footer={
        <>
          <Button variant="outline" onClick={handleClose} disabled={isSubmitting}>
            {t("cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            loading={isSubmitting}
            disabled={member === null || role === member.role}
          >
            {t("roleSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={handleSubmit} className="flex flex-col gap-4">
        {member && (
          <p className="text-sm text-muted-foreground">
            {t("roleFor", { email: member.email })}
          </p>
        )}
        <div className="flex flex-col gap-1.5">
          <label htmlFor={selectId} className="text-sm font-medium text-foreground">
            {t("columns.role")}
          </label>
          <select
            id={selectId}
            value={role}
            disabled={isSubmitting}
            onChange={(event) => {
              setRole(event.target.value as MemberRole);
              setFormError(null);
            }}
            className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {MEMBER_ROLES.map((option) => (
              <option key={option} value={option}>
                {t(memberRoleKey(option))}
              </option>
            ))}
          </select>
        </div>
        {formError && (
          <p role="alert" className="text-sm text-danger">
            {formError}
          </p>
        )}
      </form>
    </Modal>
  );
}
