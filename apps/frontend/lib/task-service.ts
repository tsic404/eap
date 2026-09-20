/**
 * Task API access layer. Functions return the unwrapped `data` payload of the
 * `{ code, data, message }` envelope; errors are re-thrown as Axios errors so
 * callers can branch on `response.status` (e.g. 422 INVALID_TRANSITION).
 */

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type { Task, TaskList, TaskListParams } from "./task-types";

export async function listTasks(params?: TaskListParams): Promise<TaskList> {
  const response = await apiClient.get<ApiEnvelope<TaskList>>(
    API_ROUTES.tasks,
    { params },
  );
  return response.data.data;
}

export async function approveTask(
  taskId: string,
  comment?: string,
): Promise<Task> {
  const response = await apiClient.post<ApiEnvelope<Task>>(
    `${API_ROUTES.tasks}/${taskId}/approve`,
    comment ? { comment } : {},
  );
  return response.data.data;
}

export async function rejectTask(taskId: string, reason: string): Promise<Task> {
  const response = await apiClient.post<ApiEnvelope<Task>>(
    `${API_ROUTES.tasks}/${taskId}/reject`,
    { reason },
  );
  return response.data.data;
}

export async function retryTask(taskId: string): Promise<Task> {
  const response = await apiClient.post<ApiEnvelope<Task>>(
    `${API_ROUTES.tasks}/${taskId}/retry`,
  );
  return response.data.data;
}
