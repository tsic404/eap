"use client";

import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "./auth-context";

/** Displays the authenticated user's profile returned by `/api/me`. */
export function UserProfile() {
  const { user } = useAuth();
  if (!user) return null;

  const avatarText = user.avatarText ?? user.name.slice(0, 1);

  return (
    <Card className="w-full max-w-md">
      <CardContent className="flex items-start gap-4 p-6">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-medium text-primary-foreground">
          {avatarText}
        </span>
        <div className="flex flex-col gap-1">
          <p className="text-base font-semibold text-foreground">{user.name}</p>
          <p className="text-sm text-muted-foreground">{user.email}</p>
          {user.department && (
            <p className="text-sm text-muted-foreground">{user.department}</p>
          )}
          <p className="text-sm text-muted-foreground">{user.role}</p>
        </div>
      </CardContent>
    </Card>
  );
}
