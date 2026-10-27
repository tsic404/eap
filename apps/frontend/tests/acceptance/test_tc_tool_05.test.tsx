/**
 * TC-tool-05 — 审批同意、拒绝流转与 24 小时超时（AC-34），员工侧可观察部分。
 *
 * Contract: C-72（任务对外暴露结果与错误信息）、C-73（状态机）、
 * C-74（驳回原因写入 result.reason）、C-75（超时取消为用户可见结果）。
 *
 * Evidence boundary: the execution chain (dispatch, queue consumer, 24h scan)
 * is the backend suite's evidence; this case fixes the employee-side surface —
 * once approved, the task center advances 已批准 → 执行中 → 已完成 through the
 * design's 10-second list poll without a manual refresh, and the card carries
 * the execution result, the rejection prompt and the timeout prompt. jsdom
 * takes no pixel or computed-style arbitration.
 */
import { act, fireEvent, screen, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoleProvider } from "@/components/auth/role-context";
import { renderWithIntl } from "@/components/test-utils";
import { TaskCenterPage } from "@/components/tasks/task-center-page";
import { ToastProvider } from "@/components/ui/toast";
import type { UserRole } from "@/lib/roles";
import { approveTask, listTasks } from "@/lib/task-service";
import type { Task } from "@/lib/task-types";

export const TC_ID = "TC-tool-05";

const { employee } = vi.hoisted(() => ({
  employee: {
    id: "user-employee",
    tenantId: "tenant-1",
    email: "employee@example.com",
    name: "张员工",
    department: null,
    role: "employee",
    avatarText: null,
  },
}));

vi.mock("@/components/auth/auth-context", () => ({
  useAuth: () => ({
    user: employee,
    tenant: { id: employee.tenantId },
    isLoading: false,
    isAuthenticated: true,
    isPasswordChangeRequired: false,
    error: null,
    login: vi.fn(),
    loginWithPassword: vi.fn(),
    logout: vi.fn(),
    refreshToken: vi.fn(),
  }),
}));

vi.mock("@/lib/task-service", () => ({
  listTasks: vi.fn(),
  approveTask: vi.fn(),
  rejectTask: vi.fn(),
  retryTask: vi.fn(),
}));

const TASK_TITLE = "导出客户数据";
const POLL_INTERVAL_MS = 10_000;

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    tenant_id: "tenant-1",
    creator_id: employee.id,
    assignee_id: null,
    type: "tool_approval",
    title: TASK_TITLE,
    priority: "high",
    status: "pending",
    payload: { tool_id: "tool-1", params: { scope: "customers" } },
    result: null,
    error_message: null,
    retry_count: 0,
    max_retries: 3,
    expires_at: "2026-10-28T00:00:00Z",
    created_at: "2026-10-27T00:00:00Z",
    resolved_at: null,
    ...overrides,
  };
}

/**
 * Answers the list query and the pending-count poll from the same stub, so the
 * task snapshots the page shows stay under the test's control. `nextSnapshot`
 * only advances on task-list reads, never on the count poll.
 */
function serveTasks(pendingCount: Task[], nextSnapshot: () => Task): void {
  vi.mocked(listTasks).mockImplementation(async (params) => {
    if (params?.status === "pending") {
      return { items: pendingCount, next_cursor: null };
    }
    return { items: [nextSnapshot()], next_cursor: null };
  });
}

function renderTaskCenter(role: UserRole) {
  return renderWithIntl(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RoleProvider role={role}>
        <ToastProvider>
          <TaskCenterPage />
        </ToastProvider>
      </RoleProvider>
    </SWRConfig>,
  );
}

/** The card list, scoped away from the filter sidebar that repeats status copy. */
function taskList() {
  return within(screen.getByRole("region", { name: "任务列表" }));
}

async function flush() {
  await act(async () => {});
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("TC-tool-05 审批流转与结果呈现", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("批准后按已批准 → 执行中 → 已完成推进，并展示执行结果", async () => {
    vi.useFakeTimers();
    const snapshots = [
      makeTask({ status: "pending" }),
      makeTask({ status: "approved" }),
      makeTask({ status: "executing" }),
      makeTask({
        status: "completed",
        resolved_at: "2026-10-27T00:00:30Z",
        result: { status_code: 200, body: { rows: 3 }, latency_ms: 42 },
      }),
    ];
    let snapshotIndex = 0;
    serveTasks([snapshots[0]], () => {
      const snapshot = snapshots[Math.min(snapshotIndex, snapshots.length - 1)];
      snapshotIndex += 1;
      return snapshot;
    });
    vi.mocked(approveTask).mockResolvedValue(snapshots[1]);

    renderTaskCenter("agent_admin");
    await flush();

    expect(taskList().getByText("待审批")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "同意" }));
    await flush();
    expect(approveTask).toHaveBeenCalledWith("task-1");
    expect(taskList().getByText("已通过")).toBeTruthy();

    // Nothing polls before the design's 10-second cadence elapses.
    await advance(POLL_INTERVAL_MS - 1_000);
    expect(taskList().getByText("已通过")).toBeTruthy();
    expect(taskList().queryByText("执行中")).toBeNull();

    await advance(1_000);
    expect(taskList().getByText("执行中")).toBeTruthy();
    expect(taskList().queryByText("执行结果")).toBeNull();

    await advance(POLL_INTERVAL_MS);
    expect(taskList().getByText("已完成")).toBeTruthy();
    expect(taskList().getByText("执行结果")).toBeTruthy();
    expect(taskList().getByText("200")).toBeTruthy();
    expect(taskList().getByText(/耗时 42 ms/)).toBeTruthy();
    expect(taskList().getByText(/"rows": 3/)).toBeTruthy();
  });

  it("驳回后员工看到「工具被拒绝」与驳回原因", async () => {
    serveTasks(
      [],
      () =>
        makeTask({
          status: "rejected",
          resolved_at: "2026-10-27T00:01:00Z",
          result: { reason: "导出范围超出授权" },
        }),
    );

    renderTaskCenter("employee");
    await flush();

    expect(taskList().getByText("工具被拒绝")).toBeTruthy();
    expect(taskList().getByText(/导出范围超出授权/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "同意" })).toBeNull();
  });

  it("超时取消的任务显示「超时取消」", async () => {
    serveTasks(
      [],
      () =>
        makeTask({
          status: "cancelled",
          resolved_at: "2026-10-28T00:00:15Z",
          result: { reason: "超时取消" },
        }),
    );

    renderTaskCenter("employee");
    await flush();

    expect(taskList().getByText("超时取消")).toBeTruthy();
    expect(taskList().getByText(/审批超过 24 小时/)).toBeTruthy();
    expect(taskList().queryByText("工具被拒绝")).toBeNull();
  });
});
