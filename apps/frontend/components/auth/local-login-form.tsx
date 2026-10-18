"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { localLoginErrorKey } from "@/lib/auth-service";
import { useAuth } from "./auth-context";

/** Email-and-password entry for an account created by a platform administrator (C-15). */
export function LocalLoginForm() {
  const { loginWithPassword } = useAuth();
  const t = useTranslations("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      // Success navigates to the workspace, so the button stays busy until then.
      await loginWithPassword(email, password);
    } catch (error) {
      setErrorKey(localLoginErrorKey(error));
      setIsSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 border-t border-border pt-4"
    >
      <h2 className="text-center text-sm font-medium text-foreground">{t("local.heading")}</h2>
      <Input
        id="local-login-email"
        name="email"
        type="email"
        autoComplete="email"
        required
        label={t("local.email")}
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Input
        id="local-login-password"
        name="password"
        type="password"
        autoComplete="current-password"
        required
        label={t("local.password")}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Button type="submit" size="lg" loading={isSubmitting} className="w-full">
        {t("local.submit")}
      </Button>
      {errorKey && (
        <p role="alert" className="text-sm text-danger">
          {t(errorKey)}
        </p>
      )}
    </form>
  );
}
