import { AuthGuard } from "@/components/auth/auth-guard";
import { ConversationDetailPage } from "@/components/chat/conversation-detail-page";

export default function ConversationDetailRoute() {
  return (
    <AuthGuard>
      <ConversationDetailPage />
    </AuthGuard>
  );
}
