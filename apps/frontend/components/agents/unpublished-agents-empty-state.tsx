import { EmptyState } from "@/components/ui/empty-state";

import { UnpublishedAgentsIllustration } from "./unpublished-agents-illustration";

/**
 * Employee-side guidance when the tenant has no published agent. The
 * marketplace and the workspace recommendation list share it so both state the
 * same fact — the administrator has not published — instead of "暂无".
 */
export function UnpublishedAgentsEmptyState() {
  return (
    <EmptyState
      variant="card"
      illustration={<UnpublishedAgentsIllustration />}
      title="您的管理员尚未发布智能体"
      description="管理员发布后，智能体会出现在这里；如需使用，请联系管理员发布。"
    />
  );
}
