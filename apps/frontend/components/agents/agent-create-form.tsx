"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { Check, ChevronLeft, ChevronRight, Eraser, RefreshCw } from "lucide-react";

import { AGENT_TYPE_LABEL } from "@/components/agents/agent-labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import {
  createAgentSchema,
  DEFAULT_AGENT_FORM_VALUES,
  MAX_TAGS,
  parseTags,
  type CreateAgentFormValues,
} from "@/lib/agent-validation";
import type { AgentType } from "@/lib/agent-types";
import { ROUTES } from "@/lib/api-routes";
import {
  createAgent,
  extractApiErrorMessage,
  isConflictError,
  listTools,
} from "@/lib/platform-service";
import { cn } from "@/lib/utils";

const DRAFT_KEY = "eap:agent-create-draft";

const STEPS = [
  { key: "basic", label: "基本信息" },
  { key: "model", label: "模型与 Prompt" },
  { key: "resources", label: "绑定资源" },
  { key: "confirm", label: "确认" },
] as const;

const STEP_FIELDS: (keyof CreateAgentFormValues)[][] = [
  ["agentId", "name", "description", "type", "category", "icon", "tags"],
  ["modelProvider", "modelId", "modelName", "prompt"],
  ["toolIds"],
  [],
];

interface ToolOption {
  toolId: string;
  name: string;
  description: string | null;
}

type ToolsStatus = "loading" | "loaded" | "error";

function orNull(value: string): string | null {
  return value === "" ? null : value;
}

/**
 * Read and validate a saved draft. The draft is an arbitrary JSON blob from
 * localStorage, so it is parsed through the schema — a corrupt or stale draft
 * (e.g. `tags` no longer a string) is discarded instead of crashing the form.
 */
function loadDraft(): CreateAgentFormValues | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const result = createAgentSchema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

function saveDraft(values: CreateAgentFormValues): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(values));
  } catch {
    // Storage may be full or blocked (private mode); draft loss is acceptable.
  }
}

function clearDraft(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Nothing else to do — the draft simply stays behind.
  }
}

function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2" aria-label="创建步骤">
      {STEPS.map((step, index) => {
        const isCurrent = index === current;
        const isDone = index < current;
        return (
          <li key={step.key} className="flex items-center gap-2">
            <span
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full text-sm font-medium",
                isDone
                  ? "bg-primary text-primary-foreground"
                  : isCurrent
                    ? "border-2 border-primary text-primary"
                    : "border border-border text-muted-foreground",
              )}
            >
              {isDone ? <Check className="h-4 w-4" /> : index + 1}
            </span>
            <span
              className={cn(
                "text-sm",
                isCurrent ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {step.label}
            </span>
            {index < STEPS.length - 1 && (
              <span className="h-px w-6 bg-border" aria-hidden="true" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function TypeSelect({
  value,
  error,
  onChange,
  onBlur,
}: {
  value: AgentType;
  error?: string;
  onChange: (value: AgentType) => void;
  onBlur: () => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="agent-type" className="text-sm font-medium text-foreground">
        类型
      </label>
      <select
        id="agent-type"
        value={value}
        onChange={(event) => onChange(event.target.value as AgentType)}
        onBlur={onBlur}
        aria-invalid={error ? true : undefined}
        className={cn(
          "h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground",
          "focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring",
          error && "border-danger",
        )}
      >
        {(Object.keys(AGENT_TYPE_LABEL) as AgentType[]).map((type) => (
          <option key={type} value={type}>
            {AGENT_TYPE_LABEL[type]}
          </option>
        ))}
      </select>
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}

/** Four-step agent creation wizard with localStorage draft persistence. */
export function AgentCreateForm() {
  const router = useRouter();
  const { toast } = useToast();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [tools, setTools] = useState<ToolOption[]>([]);
  const [toolsStatus, setToolsStatus] = useState<ToolsStatus>("loading");

  const [defaultValues] = useState<CreateAgentFormValues>(
    () => loadDraft() ?? DEFAULT_AGENT_FORM_VALUES,
  );

  const {
    register,
    handleSubmit,
    watch,
    trigger,
    reset,
    setValue,
    getValues,
    formState: { errors },
  } = useForm<CreateAgentFormValues>({
    resolver: zodResolver(createAgentSchema),
    defaultValues,
  });

  const values = watch();

  // Debounced draft persistence on any field change.
  useEffect(() => {
    const timer = setTimeout(() => saveDraft(values), 400);
    return () => clearTimeout(timer);
  }, [values]);

  const loadTools = useCallback(async () => {
    setToolsStatus("loading");
    try {
      const result = await listTools();
      setTools(
        result
          .filter((tool) => tool.status === "active")
          .map((tool) => ({
            toolId: tool.tool_id,
            name: tool.name,
            description: tool.description,
          })),
      );
      setToolsStatus("loaded");
    } catch {
      setToolsStatus("error");
    }
  }, []);

  useEffect(() => {
    void loadTools();
  }, [loadTools]);

  const parsedTags = useMemo(() => parseTags(values.tags), [values.tags]);

  const goNext = async () => {
    const valid = await trigger(STEP_FIELDS[step], { shouldFocus: true });
    if (valid) setStep((current) => Math.min(current + 1, STEPS.length - 1));
  };

  const goBack = () => setStep((current) => Math.max(current - 1, 0));

  const handleClearDraft = () => {
    clearDraft();
    reset(DEFAULT_AGENT_FORM_VALUES);
  };

  const toggleTool = (toolId: string) => {
    const current = getValues("toolIds");
    const next = current.includes(toolId)
      ? current.filter((id) => id !== toolId)
      : [...current, toolId];
    setValue("toolIds", next, { shouldDirty: true });
  };

  const onSubmit = async (formValues: CreateAgentFormValues) => {
    setSubmitting(true);
    try {
      const agent = await createAgent({
        agentId: formValues.agentId,
        name: formValues.name,
        description: orNull(formValues.description.trim()),
        type: formValues.type,
        category: orNull(formValues.category.trim()),
        icon: formValues.icon.trim() || "🤖",
        tags: parseTags(formValues.tags),
        modelProvider: orNull(formValues.modelProvider.trim()),
        modelId: orNull(formValues.modelId.trim()),
        modelName: orNull(formValues.modelName.trim()),
        prompt: orNull(formValues.prompt.trim()),
        knowledgeBaseIds: [],
        toolIds: formValues.toolIds,
      });
      clearDraft();
      toast({ type: "success", title: "创建成功" });
      router.push(ROUTES.adminAgentDetail(agent.agentId));
    } catch (error) {
      const message = isConflictError(error)
        ? "智能体标识已存在，请更换后重试"
        : extractApiErrorMessage(error);
      toast({ type: "error", title: "创建失败", description: message });
    } finally {
      setSubmitting(false);
    }
  };

  const toolIds = watch("toolIds");

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-8">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold text-foreground">创建智能体</h1>
          <p className="text-sm text-muted-foreground">按步骤配置一个新的智能体</p>
        </div>
        <Button variant="ghost" size="sm" onClick={handleClearDraft}>
          <Eraser className="h-4 w-4" />
          清除草稿
        </Button>
      </div>

      <StepIndicator current={step} />

      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-6">
        {step === 0 && (
          <div className="flex flex-col gap-4">
            <Input
              label="标识（agentId）"
              placeholder="例如 customer-service-bot"
              helperText="创建后不可修改，仅小写字母、数字、连字符"
              {...register("agentId")}
              error={errors.agentId?.message}
            />
            <Input
              label="名称"
              placeholder="请输入智能体名称"
              {...register("name")}
              error={errors.name?.message}
            />
            <Input
              label="描述"
              placeholder="简要说明智能体的用途（可选）"
              {...register("description")}
              error={errors.description?.message}
            />
            <TypeSelect
              value={values.type}
              onChange={(type) => setValue("type", type, { shouldDirty: true })}
              onBlur={() => trigger("type")}
              error={errors.type?.message}
            />
            <Input
              label="分类"
              placeholder="例如 客服 / 数据分析（可选）"
              {...register("category")}
              error={errors.category?.message}
            />
            <Input
              label="图标"
              placeholder="输入一个 emoji（可选）"
              helperText="默认 🤖"
              {...register("icon")}
              error={errors.icon?.message}
            />
            <Input
              label="标签"
              placeholder="用逗号分隔，例如 客服, 售后"
              helperText={`最多 ${MAX_TAGS} 个标签`}
              {...register("tags")}
              error={errors.tags?.message}
            />
          </div>
        )}

        {step === 1 && (
          <div className="flex flex-col gap-4">
            <Input
              label="模型提供方"
              placeholder="例如 openai / azure（可选）"
              {...register("modelProvider")}
              error={errors.modelProvider?.message}
            />
            <Input
              label="模型 ID"
              placeholder="例如 gpt-4o-mini（可选）"
              {...register("modelId")}
              error={errors.modelId?.message}
            />
            <Input
              label="模型名称"
              placeholder="展示用名称（可选）"
              {...register("modelName")}
              error={errors.modelName?.message}
            />
            <div className="flex flex-col gap-1.5">
              <label htmlFor="agent-prompt" className="text-sm font-medium text-foreground">
                系统 Prompt
              </label>
              <textarea
                id="agent-prompt"
                rows={6}
                placeholder="定义智能体的角色与行为（可选）"
                className={cn(
                  "rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground",
                  "placeholder:text-muted-foreground",
                  "focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring",
                  errors.prompt && "border-danger",
                )}
                {...register("prompt")}
              />
              {errors.prompt && <p className="text-sm text-danger">{errors.prompt.message}</p>}
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-base font-semibold text-foreground">绑定工具</h2>
              <p className="text-sm text-muted-foreground">
                选择智能体可调用的工具（可选）
              </p>
            </div>
            {toolsStatus === "loading" ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner size="sm" />
                加载工具列表…
              </div>
            ) : toolsStatus === "error" ? (
              <div className="flex flex-col items-start gap-2 rounded-lg border border-border p-4">
                <p className="text-sm text-muted-foreground">工具列表加载失败</p>
                <Button variant="outline" size="sm" onClick={() => void loadTools()}>
                  <RefreshCw className="h-4 w-4" />
                  重试
                </Button>
              </div>
            ) : tools.length === 0 ? (
              <EmptyState
                title="暂无可用工具"
                description="尚未注册启用的工具，可稍后返回绑定"
              />
            ) : (
              <div className="flex flex-col gap-2">
                {tools.map((tool) => {
                  const checked = toolIds.includes(tool.toolId);
                  return (
                    <label
                      key={tool.toolId}
                      className={cn(
                        "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                        checked ? "border-primary bg-primary-subtle" : "border-border",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTool(tool.toolId)}
                        className="mt-1 h-4 w-4"
                      />
                      <span className="flex flex-col gap-0.5">
                        <span className="text-sm font-medium text-foreground">{tool.name}</span>
                        {tool.description && (
                          <span className="text-xs text-muted-foreground">
                            {tool.description}
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              知识库绑定将在知识库模块接入后开放。
            </p>
          </div>
        )}

        {step === 3 && (
          <Card>
            <CardContent className="flex flex-col gap-3 p-5">
              <SummaryRow label="标识" value={values.agentId || "—"} />
              <SummaryRow label="名称" value={values.name || "—"} />
              <SummaryRow label="类型" value={AGENT_TYPE_LABEL[values.type]} />
              <SummaryRow label="描述" value={values.description || "—"} />
              <SummaryRow label="分类" value={values.category || "—"} />
              <SummaryRow label="图标" value={values.icon || "🤖"} />
              <div className="flex flex-col gap-1.5">
                <span className="text-sm text-muted-foreground">标签</span>
                <div className="flex flex-wrap gap-1.5">
                  {parsedTags.length === 0 ? (
                    <span className="text-sm text-foreground">—</span>
                  ) : (
                    parsedTags.map((tag) => (
                      <Badge key={tag} variant="default">
                        {tag}
                      </Badge>
                    ))
                  )}
                </div>
              </div>
              <SummaryRow
                label="模型"
                value={[values.modelProvider, values.modelId].filter(Boolean).join(" / ") || "—"}
              />
              <div className="flex flex-col gap-1.5">
                <span className="text-sm text-muted-foreground">绑定工具</span>
                <span className="text-sm text-foreground">
                  {toolIds.length === 0 ? "—" : `${toolIds.length} 个工具`}
                </span>
              </div>
            </CardContent>
          </Card>
        )}

        <div className="flex items-center justify-between border-t border-border pt-4">
          <Button
            type="button"
            variant="outline"
            onClick={goBack}
            disabled={step === 0 || submitting}
          >
            <ChevronLeft className="h-4 w-4" />
            上一步
          </Button>
          {step < STEPS.length - 1 ? (
            <Button type="button" onClick={goNext}>
              下一步
              <ChevronRight className="h-4 w-4" />
            </Button>
          ) : (
            <Button type="submit" loading={submitting}>
              创建智能体
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="break-all text-right text-sm text-foreground">{value}</span>
    </div>
  );
}
