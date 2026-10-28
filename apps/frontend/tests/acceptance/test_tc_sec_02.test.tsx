/**
 * TC-sec-02 — empty-state guidance on both sides (AC-43).
 *
 * An admin list with no rows shows a create-first guidance card that carries
 * the next step, and exposes no list at all: every populated card grid on these
 * screens renders as a named `list`, so a regression to "card + empty grid"
 * fails the empty-state assertion instead of passing silently. Read-only roles
 * see the same card without any create entry.
 *
 * On the employee side a tenant without published agents shows the
 * unpublished-agents illustration and copy on the marketplace and on the
 * workspace recommendation section — while a filtered-to-nothing marketplace
 * keeps its "no match" state, because published agents do exist there.
 * Browser-side replay: `e2e/journeys/tc-sec-02-empty-guidance.spec.ts`.
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoleProvider } from "@/components/auth/role-context";
import { ToastProvider } from "@/components/ui/toast";

import { renderWithIntl } from "@/components/test-utils";

const state = vi.hoisted(() => ({
  pushMock: vi.fn(),
  agents: [] as unknown[],
  agentsLoading: false,
  agentsError: undefined as Error | undefined,
  knowledgeBases: [] as unknown[],
  tools: [] as unknown[],
  homeLoading: false,
  homeError: undefined as Error | undefined,
  userHome: {
    recommendedAgents: [] as unknown[],
    recentConversations: [] as unknown[],
    pendingTaskCount: 0,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.pushMock }),
}));

// The profile card needs the auth provider and is not part of an empty state.
vi.mock("@/components/auth/user-profile", () => ({
  UserProfile: () => null,
}));

vi.mock("@/lib/use-dashboard", () => ({
  useUserHome: () => ({
    data: state.userHome,
    error: state.homeError,
    isLoading: state.homeLoading,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/use-knowledge", () => ({
  useKnowledgeBases: () => ({
    data: { items: state.knowledgeBases, total: state.knowledgeBases.length },
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/use-tools", () => ({
  useTools: () => ({
    data: state.tools,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/use-agents", () => ({
  useAllAgents: () => ({
    data: state.agents,
    error: state.agentsError,
    isLoading: state.agentsLoading,
    mutate: vi.fn(),
  }),
}));

import { AgentListPage } from "@/components/agents/agent-list-page";
import { AgentMarketplace } from "@/components/agents/agent-marketplace";
import { UserHomePage } from "@/components/dashboard/user-home-page";
import { KnowledgeListPage } from "@/components/knowledge/knowledge-list-page";
import { ToolListPage } from "@/components/tools/tool-list-page";
import type { Agent } from "@/lib/agent-types";
import type { KnowledgeBase } from "@/lib/knowledge-types";
import type { ToolDetail } from "@/lib/tool-types";
import type { UserRole } from "@/lib/roles";

export const TC_ID = "TC-sec-02";

const AGENT: Agent = {
  agentId: "agent-1",
  tenantId: "tenant-1",
  difyAppId: "app-1",
  name: "售前助手",
  description: "回答售前咨询",
  type: "chat",
  category: null,
  icon: null,
  tags: ["售前"],
  status: "published",
  modelId: null,
  modelName: null,
  modelProvider: null,
  prompt: null,
  version: 1,
  createdBy: null,
  publishedAt: null,
  publishedBy: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
};

const KNOWLEDGE_BASE: KnowledgeBase = {
  kb_id: "kb-1",
  name: "产品手册",
  description: "产品资料",
  type: "business",
  indexing_status: "ready",
  doc_count: 3,
  chunk_count: 12,
  created_at: "2026-01-01T00:00:00Z",
};

const TOOL: ToolDetail = {
  tool_id: "tool-1",
  tenant_id: "tenant-1",
  name: "订单查询",
  description: "查询订单状态",
  type: "http",
  endpoint: "https://example.test/orders",
  method: "GET",
  risk_level: "low",
  permission_mode: "auto",
  auth_type: "none",
  auth_config: null,
  timeout_ms: 5000,
  retry_policy: null,
  circuit_breaker: null,
  status: "active",
  created_by: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderKnowledgeList(role: UserRole = "platform_admin") {
  return renderWithIntl(
    <RoleProvider role={role}>
      <ToastProvider>
        <KnowledgeListPage />
      </ToastProvider>
    </RoleProvider>,
  );
}

function renderToolList(role: UserRole = "platform_admin") {
  return renderWithIntl(
    <RoleProvider role={role}>
      <ToolListPage />
    </RoleProvider>,
  );
}

function renderAgentList(role: UserRole = "platform_admin") {
  return renderWithIntl(
    <RoleProvider role={role}>
      <AgentListPage />
    </RoleProvider>,
  );
}

function renderMarketplace(role: UserRole = "employee") {
  return renderWithIntl(
    <RoleProvider role={role}>
      <AgentMarketplace />
    </RoleProvider>,
  );
}

function renderUserHome() {
  return renderWithIntl(
    <RoleProvider role="employee">
      <UserHomePage />
    </RoleProvider>,
  );
}

describe("TC-sec-02 admin empty states", () => {
  beforeEach(() => {
    state.pushMock.mockReset();
    state.agents = [];
    state.agentsLoading = false;
    state.agentsError = undefined;
    state.knowledgeBases = [];
    state.tools = [];
  });

  it("知识库列表空态呈现创建第一个知识库的引导卡片且不渲染列表", async () => {
    renderKnowledgeList();

    const card = screen.getByRole("region", { name: "创建第一个知识库" });
    expect(within(card).getByText(/上传文档/)).toBeTruthy();

    fireEvent.click(within(card).getByRole("button", { name: "新建知识库" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "知识库列表" })).toBeNull();
  });

  it("工具列表空态呈现创建第一个工具的引导卡片且不渲染列表", () => {
    renderToolList();

    const card = screen.getByRole("region", { name: "创建第一个工具" });
    fireEvent.click(within(card).getByRole("button", { name: "新建工具" }));
    expect(state.pushMock).toHaveBeenCalledWith("/admin/tools/new");
    expect(screen.queryByRole("list", { name: "工具列表" })).toBeNull();
  });

  it("智能体管理页空态呈现创建第一个智能体的引导卡片且不渲染列表", () => {
    renderAgentList();

    expect(screen.getByRole("heading", { name: "智能体" })).toBeTruthy();
    const card = screen.getByRole("region", { name: "创建第一个智能体" });
    fireEvent.click(within(card).getByRole("button", { name: "新建智能体" }));
    expect(state.pushMock).toHaveBeenCalledWith("/admin/agents/new");
    expect(screen.queryByRole("list", { name: "智能体列表" })).toBeNull();
  });

  it("有数据时知识库行以具名列表呈现", () => {
    state.knowledgeBases = [KNOWLEDGE_BASE];
    renderKnowledgeList();

    const list = screen.getByRole("list", { name: "知识库列表" });
    expect(within(list).getByText("产品手册")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "创建第一个知识库" })).toBeNull();
  });

  it("有数据时工具行以具名列表呈现", () => {
    state.tools = [TOOL];
    renderToolList();

    const list = screen.getByRole("list", { name: "工具列表" });
    expect(within(list).getByText("订单查询")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "创建第一个工具" })).toBeNull();
  });

  it("有数据时智能体卡片是可用键盘进入的详情链接", () => {
    state.agents = [AGENT];
    renderAgentList();

    const list = screen.getByRole("list", { name: "智能体列表" });
    const link = within(list).getByRole("link", { name: /售前助手/ });
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/admin/agents/agent-1");
    expect(link.getAttribute("tabindex")).toBeNull();
    expect(screen.queryByRole("region", { name: "创建第一个智能体" })).toBeNull();
  });

  it("只读角色看到智能体引导卡片但没有创建入口", () => {
    renderAgentList("knowledge_admin");

    const card = screen.getByRole("region", { name: "创建第一个智能体" });
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "新建智能体" })).toBeNull();
  });

  it("审计员看到知识库引导卡片但没有创建入口", () => {
    renderKnowledgeList("auditor");

    const card = screen.getByRole("region", { name: "创建第一个知识库" });
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "新建知识库" })).toBeNull();
  });

  it("审计员看到工具引导卡片但没有创建入口", () => {
    renderToolList("auditor");

    const card = screen.getByRole("region", { name: "创建第一个工具" });
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "新建工具" })).toBeNull();
  });
});

describe("TC-sec-02 employee empty states", () => {
  beforeEach(() => {
    state.pushMock.mockReset();
    state.agents = [];
    state.agentsLoading = false;
    state.agentsError = undefined;
    state.homeLoading = false;
    state.homeError = undefined;
    state.userHome = {
      recommendedAgents: [],
      recentConversations: [],
      pendingTaskCount: 0,
    };
  });

  it("智能体广场无已发布智能体时呈现未发布插画与引导文案", () => {
    renderMarketplace("employee");

    const guidance = screen.getByRole("region", {
      name: "您的管理员尚未发布智能体",
    });
    expect(within(guidance).getByRole("img", { name: "尚未发布智能体" })).toBeTruthy();
    expect(within(guidance).getByText(/联系管理员/)).toBeTruthy();
  });

  it("智能体广场空态不渲染列表或表格占位", () => {
    renderMarketplace("employee");

    expect(
      screen.queryByRole("list", { name: "智能体广场列表" }),
    ).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("暂无已发布的智能体")).toBeNull();
  });

  it("加载中不呈现未发布引导", () => {
    state.agentsLoading = true;
    const marketplace = renderMarketplace("employee");
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
    marketplace.unmount();

    state.homeLoading = true;
    renderUserHome();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
  });

  it("请求失败时不呈现未发布引导", () => {
    state.agentsError = new Error("network");
    const marketplace = renderMarketplace("employee");
    expect(screen.getByText("加载失败")).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
    marketplace.unmount();

    state.homeError = new Error("network");
    renderUserHome();
    expect(screen.getByText("加载失败")).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
  });

  it("智能体广场有已发布智能体时渲染卡片且不出现未发布引导", () => {
    state.agents = [AGENT];
    renderMarketplace("employee");

    const list = screen.getByRole("list", { name: "智能体广场列表" });
    expect(within(list).getByText("售前助手")).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
  });

  it("搜索无结果时保留未找到匹配结果，不冒用未发布引导", () => {
    state.agents = [AGENT];
    renderMarketplace("employee");

    fireEvent.change(screen.getByLabelText("搜索智能体"), {
      target: { value: "不存在的智能体" },
    });

    expect(screen.getByText("未找到匹配结果")).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
  });

  it("用户首页推荐智能体为空时呈现未发布插画与引导文案", () => {
    renderUserHome();

    const guidance = screen.getByRole("region", {
      name: "您的管理员尚未发布智能体",
    });
    expect(within(guidance).getByRole("img", { name: "尚未发布智能体" })).toBeTruthy();
    expect(within(guidance).getByText(/联系管理员/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "推荐智能体列表" })).toBeNull();
    // The conversation section keeps its own empty state.
    expect(screen.getByText("暂无会话")).toBeTruthy();
  });

  it("用户首页有推荐智能体时不出现未发布引导", () => {
    state.userHome = {
      recommendedAgents: [AGENT],
      recentConversations: [],
      pendingTaskCount: 0,
    };
    renderUserHome();

    const list = screen.getByRole("list", { name: "推荐智能体列表" });
    expect(within(list).getByText("售前助手")).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "您的管理员尚未发布智能体" }),
    ).toBeNull();
  });
});
