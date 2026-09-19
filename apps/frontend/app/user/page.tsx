import { AuthGuard } from "@/components/auth/auth-guard";
import { UserHomePage } from "@/components/dashboard/user-home-page";
import { AppShell } from "@/components/layout/app-shell";
import { UserSidebar } from "@/components/layout/user-sidebar";

export default function UserPage() {
  return (
    <AuthGuard>
      <AppShell sidebar={<UserSidebar />}>
        <main className="mx-auto max-w-5xl px-6 py-8">
          <UserHomePage />
        </main>
      </AppShell>
    </AuthGuard>
  );
}
