import { AuthGuard } from "@/components/auth/auth-guard";
import { ConversationListPage } from "@/components/chat/conversation-list-page";
import { Header } from "@/components/layout/header";

export default function ConversationsPage() {
  return (
    <AuthGuard>
      <div className="min-h-screen">
        <Header />
        <main className="mx-auto max-w-3xl px-6 py-8">
          <h1 className="mb-6 text-2xl font-bold text-foreground">会话</h1>
          <ConversationListPage />
        </main>
      </div>
    </AuthGuard>
  );
}
