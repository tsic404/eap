import { AuthGuard } from "@/components/auth/auth-guard";
import { UserProfile } from "@/components/auth/user-profile";
import { Header } from "@/components/layout/header";

export default function UserPage() {
  return (
    <AuthGuard>
      <div className="min-h-screen">
        <Header />
        <main className="mx-auto max-w-5xl px-6 py-8">
          <h1 className="mb-4 text-2xl font-bold text-foreground">用户工作区</h1>
          <UserProfile />
        </main>
      </div>
    </AuthGuard>
  );
}
