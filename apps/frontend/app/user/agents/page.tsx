import { AgentMarketplace } from "@/components/agents/agent-marketplace";
import { AuthGuard } from "@/components/auth/auth-guard";
import { Header } from "@/components/layout/header";

export default function AgentMarketplacePage() {
  return (
    <AuthGuard>
      <div className="min-h-screen">
        <Header />
        <main className="mx-auto max-w-6xl px-6 py-8">
          <AgentMarketplace />
        </main>
      </div>
    </AuthGuard>
  );
}
