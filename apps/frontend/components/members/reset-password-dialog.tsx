"use client";

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-service";
import {
  isMemberForbiddenError,
  isMemberPasswordValidationError,
  isUnavailableError,
  resetMemberPassword,
} from "@/lib/member-service";
import type { Member } from "@/lib/member-types";

export interface ResetPasswordDialogProps {
  member: Member | null;
  onClose: () => void;
  onReset: (member: Member) => void;
}

/**
 * Sets a new password supplied by the administrator (C-115). The backend decides
 * the format; this dialog adds no rule of its own and never renders a password.
 */
export function ResetPasswordDialog({ member, onClose, onReset }: ResetPasswordDialogProps) {
  const t = useTranslations("members");
  const formId = useId();
  const [password, setPassword] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (member === null) return;
    setPassword("");
    setFieldError(null);
    setFormError(null);
  }, [member]);

  const handleClose = () => {
    if (isSubmitting) return;
    onClose();
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || member === null) return;
    if (password === "") {
      setFieldError(t("errors.requiredNewPassword"));
      return;
    }

    setFieldError(null);
    setFormError(null);
    setIsSubmitting(true);
    try {
      const updated = await resetMemberPassword(member.id, password);
      onReset(updated);
    } catch (error) {
      if (isUnavailableError(error)) {
        setFormError(t("errors.unavailable"));
      } else if (isMemberForbiddenError(error)) {
        setFormError(t("errors.forbidden"));
      } else if (isMemberPasswordValidationError(error)) {
        // Both reset rules share one detail shape; the typed value separates
        // the length rule from the unchanged rule without reading prose.
        const length = [...password].length;
        setFieldError(
          length < PASSWORD_MIN_LENGTH || length > PASSWORD_MAX_LENGTH
            ? t("errors.passwordLength", {
                min: PASSWORD_MIN_LENGTH,
                max: PASSWORD_MAX_LENGTH,
              })
            : t("errors.passwordUnchanged"),
        );
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
      title={t("resetTitle")}
      footer={
        <>
          <Button variant="outline" onClick={handleClose} disabled={isSubmitting}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={formId} loading={isSubmitting}>
            {t("resetSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={handleSubmit} className="flex flex-col gap-4">
        {member && (
          <p className="text-sm text-muted-foreground">
            {t("resetFor", { email: member.email })}
          </p>
        )}
        <Input
          label={t("newPassword")}
          type="password"
          autoComplete="new-password"
          value={password}
          error={fieldError ?? undefined}
          disabled={isSubmitting}
          onChange={(event) => {
            setPassword(event.target.value);
            setFieldError(null);
          }}
        />
        {formError && (
          <p role="alert" className="text-sm text-danger">
            {formError}
          </p>
        )}
      </form>
    </Modal>
  );
}
