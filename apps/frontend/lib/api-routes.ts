/**
 * Centralised API route constants.
 *
 * Browser navigations (SSO login/callback) use origin-relative `/api/*` paths
 * that nginx proxies to the FastAPI backend. axios calls use routes relative
 * to `NEXT_PUBLIC_API_BASE_URL` (see `lib/http-client.ts`).
 */

/** Auth endpoints reached by full browser navigation. */
export const AUTH_ROUTES = {
  login: "/api/auth/login",
  callback: "/api/auth/callback",
} as const;

/** axios API routes, relative to `NEXT_PUBLIC_API_BASE_URL`. */
export const API_ROUTES = {
  refresh: "/auth/refresh",
  logout: "/auth/logout",
  me: "/me",
  agents: "/agents",
  tools: "/tools",
  knowledgeBases: "/knowledge-bases",
  conversations: "/conversations",
  runLogs: "/run-logs",
} as const;

/** Frontend page routes. */
export const ROUTES = {
  login: "/login",
  home: "/user",
  agents: "/user/agents",
  adminHome: "/admin",
  adminAgentsNew: "/admin/agents/new",
  adminAgentDetail: (agentId: string) => `/admin/agents/${agentId}`,
  adminKnowledge: "/admin/knowledge",
  adminKnowledgeDetail: (kbId: string) => `/admin/knowledge/${kbId}`,
  adminTools: "/admin/tools",
  adminToolsNew: "/admin/tools/new",
  adminToolDetail: (toolId: string) => `/admin/tools/${toolId}`,
  conversations: "/user/conversations",
  conversationDetail: (conversationId: string) => `/user/conversations/${conversationId}`,
  adminRunLogDetail: (traceId: string) => `/admin/run-logs/${traceId}`,
} as const;

/** Identity provider used by the single SSO login button. */
export const DEFAULT_SSO_PROVIDER = "oidc";
