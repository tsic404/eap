import { z } from "zod";

import { AGENT_TYPES } from "./agent-types";
import { isValidAgentId } from "./validators";

export const MAX_TAGS = 10;

/** Split a comma/Chinese-comma separated string into trimmed, non-empty tags. */
export function parseTags(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
}

/**
 * Validation schema for the four-step agent creation wizard.
 *
 * `agentId` and `name` are trimmed before validation so a whitespace-only
 * value is rejected by the same trimmed value that is later submitted.
 */
export const createAgentSchema = z.object({
  agentId: z
    .string()
    .trim()
    .min(1, "请输入标识")
    .refine(isValidAgentId, "仅小写字母、数字、连字符，长度 3-64，首尾不能是连字符"),
  name: z.string().trim().min(1, "请输入名称").max(255, "名称最多 255 个字符"),
  description: z.string().max(500, "描述最多 500 个字符"),
  type: z.enum(AGENT_TYPES),
  category: z.string().max(100, "分类最多 100 个字符"),
  icon: z.string().max(50, "图标最多 50 个字符"),
  tags: z.string().refine((value) => parseTags(value).length <= MAX_TAGS, {
    message: `最多 ${MAX_TAGS} 个标签`,
  }),
  modelProvider: z.string().max(255, "模型提供方最多 255 个字符"),
  modelId: z.string().max(255, "模型 ID 最多 255 个字符"),
  modelName: z.string().max(255, "模型名称最多 255 个字符"),
  prompt: z.string(),
  toolIds: z.array(z.string()),
});

export type CreateAgentFormValues = z.infer<typeof createAgentSchema>;

export const DEFAULT_AGENT_FORM_VALUES: CreateAgentFormValues = {
  agentId: "",
  name: "",
  description: "",
  type: "chat",
  category: "",
  icon: "🤖",
  tags: "",
  modelProvider: "",
  modelId: "",
  modelName: "",
  prompt: "",
  toolIds: [],
};
