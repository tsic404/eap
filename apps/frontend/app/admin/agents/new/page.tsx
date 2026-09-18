import { AgentCreateForm } from "@/components/agents/agent-create-form";
import { Header } from "@/components/layout/header";

export default function AgentCreatePage() {
  return (
    <div className="min-h-screen">
      <Header />
      <AgentCreateForm />
    </div>
  );
}
