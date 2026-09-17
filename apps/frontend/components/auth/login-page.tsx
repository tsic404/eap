"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DEFAULT_SSO_PROVIDER } from "@/lib/api-routes";
import { useAuth } from "./auth-context";

/** Centred login card with a single SSO entry point and an error state. */
export function LoginPage() {
  const { login, error } = useAuth();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setQueryError(new URLSearchParams(window.location.search).get("error"));
    }
  }, []);

  const handleLogin = () => {
    setIsSubmitting(true);
    login(DEFAULT_SSO_PROVIDER);
  };

  const displayError = error ?? queryError;

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle>登录</CardTitle>
          <CardDescription>使用企业账号登录 EAP</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Button
            size="lg"
            loading={isSubmitting}
            onClick={handleLogin}
            className="w-full"
          >
            SSO 登录
          </Button>
          {displayError && (
            <p role="alert" className="text-sm text-danger">
              {displayError}
            </p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
