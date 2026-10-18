"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ROUTES } from "@/lib/api-routes";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  changePassword,
  passwordChangeErrorKey,
} from "@/lib/auth-service";
import { clearRoleCookie } from "@/lib/role-store";
import { setAccessToken } from "@/lib/token-store";

/** Time the success notice stays readable before the account signs in again. */
const SUCCESS_REDIRECT_DELAY_MS = 1200;

interface FieldErrors {
  current?: string;
  next?: string;
  confirm?: string;
}

/**
 * Password change form (C-113).
 *
 * The bounds mirror the backend rule — 8–128 code points, counted the way the
 * server's `len()` does, different from the current password — so a violation
 * is answered at the field, not after a round-trip. Success ends every refresh
 * credential the account held, so the local session is dropped and the account
 * signs in again with the new one.
 */
export function PasswordChangeForm() {
  const t = useTranslations("password");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [submitErrorKey, setSubmitErrorKey] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isChanged, setIsChanged] = useState(false);
  const redirectTimer = useRef<number | null>(null);

  useEffect(() => () => window.clearTimeout(redirectTimer.current ?? undefined), []);

  const validate = (): FieldErrors => {
    const errors: FieldErrors = {};
    // Code points, not UTF-16 code units: the server judges with Python's len().
    const nextLength = [...newPassword].length;
    if (currentPassword.length === 0) {
      errors.current = t("errors.currentRequired");
    }
    if (nextLength < PASSWORD_MIN_LENGTH || nextLength > PASSWORD_MAX_LENGTH) {
      errors.next = t("errors.length");
    } else if (newPassword === currentPassword) {
      errors.next = t("errors.unchanged");
    }
    if (confirmation !== newPassword) {
      errors.confirm = t("errors.mismatch");
    }
    return errors;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || isChanged) return;

    const errors = validate();
    setFieldErrors(errors);
    setSubmitErrorKey(null);
    if (Object.keys(errors).length > 0) return;

    setIsSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
    } catch (error) {
      setSubmitErrorKey(passwordChangeErrorKey(error));
      setIsSubmitting(false);
      return;
    }

    // Every refresh family was revoked with the change, so the access token is
    // the only credential left and it must not outlive the redirect.
    setAccessToken(null);
    clearRoleCookie();
    setIsSubmitting(false);
    setIsChanged(true);
    redirectTimer.current = window.setTimeout(
      () => window.location.assign(ROUTES.login),
      SUCCESS_REDIRECT_DELAY_MS,
    );
  };

  if (isChanged) {
    return (
      <p role="alert" className="text-sm text-success">
        {t("success")}
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Input
        id="password-current"
        name="currentPassword"
        type="password"
        autoComplete="current-password"
        label={t("current")}
        value={currentPassword}
        onChange={(event) => setCurrentPassword(event.target.value)}
        error={fieldErrors.current}
      />
      <Input
        id="password-next"
        name="newPassword"
        type="password"
        autoComplete="new-password"
        label={t("next")}
        value={newPassword}
        onChange={(event) => setNewPassword(event.target.value)}
        error={fieldErrors.next}
      />
      <Input
        id="password-confirm"
        name="confirmPassword"
        type="password"
        autoComplete="new-password"
        label={t("confirm")}
        value={confirmation}
        onChange={(event) => setConfirmation(event.target.value)}
        error={fieldErrors.confirm}
      />
      <Button type="submit" size="lg" loading={isSubmitting} className="w-full">
        {t("submit")}
      </Button>
      {submitErrorKey && (
        <p role="alert" className="text-sm text-danger">
          {t(submitErrorKey)}
        </p>
      )}
    </form>
  );
}
