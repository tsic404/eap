"use client";

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-service";
import {
  createMember,
  isAccountExistsError,
  isMemberEmailValidationError,
  isMemberForbiddenError,
  isMemberPasswordValidationError,
  isUnavailableError,
} from "@/lib/member-service";
import type { Member } from "@/lib/member-types";

export interface CreateMemberDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (member: Member) => void;
}

/** Creates one local account; the initial password is never shown again (C-114/C-115). */
export function CreateMemberDialog({ open, onClose, onCreated }: CreateMemberDialogProps) {
  const t = useTranslations("members");
  const formId = useId();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Every opening starts from a clean form.
  useEffect(() => {
    if (!open) return;
    setEmail("");
    setName("");
    setPassword("");
    setEmailError(null);
    setNameError(null);
    setPasswordError(null);
    setFormError(null);
  }, [open]);

  const handleClose = () => {
    if (isSubmitting) return;
    onClose();
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) return;

    const trimmedEmail = email.trim();
    const trimmedName = name.trim();
    const errors = {
      email: trimmedEmail === "" ? t("errors.requiredEmail") : null,
      name: trimmedName === "" ? t("errors.requiredName") : null,
      password: password === "" ? t("errors.requiredPassword") : null,
    };
    setEmailError(errors.email);
    setNameError(errors.name);
    setPasswordError(errors.password);
    setFormError(null);
    if (errors.email || errors.name || errors.password) return;

    setIsSubmitting(true);
    try {
      const member = await createMember({
        email: trimmedEmail,
        name: trimmedName,
        password,
      });
      onCreated(member);
    } catch (error) {
      if (isAccountExistsError(error)) {
        setEmailError(t("errors.accountExists"));
      } else if (isMemberEmailValidationError(error)) {
        setEmailError(t("errors.emailDomain"));
      } else if (isMemberPasswordValidationError(error)) {
        setPasswordError(
          t("errors.passwordLength", {
            min: PASSWORD_MIN_LENGTH,
            max: PASSWORD_MAX_LENGTH,
          }),
        );
      } else if (isUnavailableError(error)) {
        setFormError(t("errors.unavailable"));
      } else if (isMemberForbiddenError(error)) {
        setFormError(t("errors.forbidden"));
      } else {
        setFormError(t("errors.generic"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={t("createTitle")}
      footer={
        <>
          <Button variant="outline" onClick={handleClose} disabled={isSubmitting}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={formId} loading={isSubmitting}>
            {t("createSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Input
          label={t("email")}
          type="email"
          autoComplete="off"
          value={email}
          error={emailError ?? undefined}
          disabled={isSubmitting}
          onChange={(event) => {
            setEmail(event.target.value);
            setEmailError(null);
          }}
        />
        <Input
          label={t("name")}
          autoComplete="off"
          value={name}
          error={nameError ?? undefined}
          disabled={isSubmitting}
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
        />
        <Input
          label={t("initialPassword")}
          type="password"
          autoComplete="new-password"
          value={password}
          error={passwordError ?? undefined}
          disabled={isSubmitting}
          onChange={(event) => {
            setPassword(event.target.value);
            setPasswordError(null);
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
