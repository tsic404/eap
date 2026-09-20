import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/layout/app-shell";
import { UserSidebar } from "@/components/layout/user-sidebar";
import { TaskCenterPage } from "@/components/tasks/task-center-page";

export default function UserTasksPage() {
  return (
    <AuthGuard>
      <AppShell sidebar={<UserSidebar active="tasks" />}>
        <main className="mx-auto max-w-6xl px-6 py-8">
          <TaskCenterPage />
        </main>
      </AppShell>
    </AuthGuard>
  );
}
