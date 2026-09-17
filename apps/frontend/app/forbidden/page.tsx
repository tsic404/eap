import { AdminForbidden } from "@/components/auth/admin-forbidden";

export const metadata = {
  title: "无管理权限",
};

export default function ForbiddenPage() {
  return <AdminForbidden />;
}
