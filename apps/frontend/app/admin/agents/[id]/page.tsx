import { AgentDetailPage } from "@/components/agents/agent-detail-page";
import { Header } from "@/components/layout/header";

export default function AgentDetailRoute() {
  return (
    <div className="min-h-screen">
      <Header />
      <AgentDetailPage />
    </div>
  );
}
