import Link from "next/link";

/**
 * Friendly permission notice shown to employees who reach the admin workspace
 * (architecture v8.0 §31.2.2). Server-compatible: no client hooks.
 */
export function AdminForbidden() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-2xl font-bold text-foreground">无管理权限</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        您没有管理权限。如需访问管理端，请联系平台管理员授予 agent_admin
        或更高角色。
      </p>
      <Link
        href="/"
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary-hover"
      >
        返回首页
      </Link>
    </main>
  );
}
