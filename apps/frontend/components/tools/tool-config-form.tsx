"use client";

import { useEffect, useId } from "react";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { createTool, updateTool } from "@/lib/tool-service";
import { cn } from "@/lib/utils";
import {
  AUTH_TYPES,
  HTTP_METHODS,
  PERMISSION_MODES,
  RISK_LEVELS,
  TOOL_TYPES,
} from "@/lib/tool-types";
import type { RiskLevel, ToolDetail } from "@/lib/tool-types";

import {
  AUTH_TYPE_LABEL,
  PERMISSION_MODE_LABEL,
  RISK_LEVEL_LABEL,
  TOOL_TYPE_LABEL,
} from "./tool-labels";

const TOOL_ID_REGEX = /^[a-z0-9][a-z0-9-]*$/;

const toolSchema = z.object({
  name: z.string().trim().min(1, "请输入名称").max(100, "名称最多 100 个字符"),
  tool_id: z
    .string()
    .trim()
    .min(1, "请输入标识")
    .max(100, "标识最多 100 个字符")
    .regex(TOOL_ID_REGEX, "仅小写字母、数字、连字符，且以字母或数字开头"),
  type: z.enum(TOOL_TYPES),
  description: z.string().max(500, "描述最多 500 个字符"),
  risk_level: z.enum(RISK_LEVELS),
  permission_mode: z.enum(PERMISSION_MODES),
  endpoint: z
    .string()
    .trim()
    .min(1, "请输入端点")
    .max(2048, "端点最多 2048 个字符")
    .regex(/^https?:\/\/\S+$/i, "请输入合法的 http/https URL"),
  method: z.enum(HTTP_METHODS),
  auth_type: z.enum(AUTH_TYPES),
  timeout_ms: z.coerce
    .number()
    .int("超时必须为整数")
    .min(1, "超时至少 1ms"),
});

type ToolFormValues = z.infer<typeof toolSchema>;

const DEFAULT_VALUES: ToolFormValues = {
  name: "",
  tool_id: "",
  type: "http",
  description: "",
  risk_level: "medium",
  permission_mode: "auto",
  endpoint: "",
  method: "POST",
  auth_type: "none",
  timeout_ms: 10000,
};

const SELECT_CLASS =
  "h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

const CHOICE_CARD_CLASS =
  "flex cursor-pointer items-center justify-center rounded-lg border px-3 py-3 text-center text-sm transition-colors focus-within:ring-2 focus-within:ring-ring";
const CHOICE_CARD_SELECTED =
  "border-primary bg-primary-subtle font-medium text-primary";
const CHOICE_CARD_IDLE =
  "border-border bg-background text-foreground hover:border-primary";

/** Risk slider bands read left to right as low → medium → high. */
const RISK_BAND_CLASS: Record<RiskLevel, string> = {
  low: "bg-success",
  medium: "bg-warning",
  high: "bg-danger",
};

function oneOf<T extends readonly string[]>(
  value: string,
  options: T,
  fallback: T[number],
): T[number] {
  return (options as readonly string[]).includes(value)
    ? (value as T[number])
    : fallback;
}

function toFormValues(tool: ToolDetail): ToolFormValues {
  return {
    name: tool.name,
    tool_id: tool.tool_id,
    type: oneOf(tool.type, TOOL_TYPES, "http"),
    description: tool.description ?? "",
    risk_level: oneOf(tool.risk_level, RISK_LEVELS, "medium"),
    permission_mode: oneOf(tool.permission_mode, PERMISSION_MODES, "auto"),
    endpoint: tool.endpoint ?? "",
    method: oneOf(tool.method ?? "POST", HTTP_METHODS, "POST"),
    auth_type: oneOf(tool.auth_type ?? "none", AUTH_TYPES, "none"),
    timeout_ms: tool.timeout_ms,
  };
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-foreground">{label}</label>
      {children}
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}

/** Labelled radiogroup: card options render as native radios for one choice. */
function ChoiceField({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-sm font-medium text-foreground">
        {label}
      </span>
      <div role="radiogroup" aria-labelledby={labelId}>
        {children}
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}

export interface ToolConfigFormProps {
  /** Present → edit mode; absent → create mode. */
  tool?: ToolDetail;
  onSaved?: (tool: ToolDetail) => void;
}

/** Tool registration/configuration form (type + endpoint + auth + risk + permission). */
export function ToolConfigForm({ tool, onSaved }: ToolConfigFormProps) {
  const router = useRouter();
  const { toast } = useToast();
  const isEdit = tool !== undefined;

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<ToolFormValues>({
    resolver: zodResolver(toolSchema),
    defaultValues: tool ? toFormValues(tool) : DEFAULT_VALUES,
  });

  const selectedType = watch("type");
  const selectedPermissionMode = watch("permission_mode");
  const riskLevel = watch("risk_level");
  const riskIndex = RISK_LEVELS.indexOf(riskLevel);

  useEffect(() => {
    reset(tool ? toFormValues(tool) : DEFAULT_VALUES);
  }, [tool, reset]);

  const onSubmit = async (values: ToolFormValues) => {
    const payload = {
      name: values.name,
      description: values.description.trim() === "" ? null : values.description,
      type: values.type,
      risk_level: values.risk_level,
      permission_mode: values.permission_mode,
      endpoint: values.endpoint,
      method: values.method,
      auth_type: values.auth_type,
      timeout_ms: values.timeout_ms,
    };
    try {
      if (isEdit && tool) {
        const updated = await updateTool(tool.tool_id, payload);
        toast({ type: "success", title: "工具已更新" });
        onSaved?.(updated);
      } else {
        const created = await createTool({ ...payload, tool_id: values.tool_id });
        toast({ type: "success", title: "工具已创建" });
        onSaved?.(created);
        router.replace(ROUTES.adminToolDetail(created.tool_id));
      }
    } catch (submitError) {
      toast({
        type: "error",
        title: isEdit ? "更新失败" : "创建失败",
        description: extractApiErrorMessage(submitError),
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{isEdit ? "工具配置" : "新建工具"}</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="名称"
              placeholder="例如：天气查询"
              error={errors.name?.message}
              {...register("name")}
            />
            <Input
              label="标识（tool_id）"
              placeholder="例如：weather-query"
              disabled={isEdit}
              error={errors.tool_id?.message}
              helperText={isEdit ? "标识创建后不可修改" : undefined}
              {...register("tool_id")}
            />
          </div>

          <Field label="描述" error={errors.description?.message}>
            <textarea
              rows={2}
              placeholder="可选"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
              {...register("description")}
            />
          </Field>

          <ChoiceField label="类型" error={errors.type?.message}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {TOOL_TYPES.map((value) => (
                <label
                  key={value}
                  className={cn(
                    CHOICE_CARD_CLASS,
                    selectedType === value
                      ? CHOICE_CARD_SELECTED
                      : CHOICE_CARD_IDLE,
                  )}
                >
                  <input
                    type="radio"
                    value={value}
                    className="sr-only"
                    {...register("type")}
                  />
                  {TOOL_TYPE_LABEL[value]}
                </label>
              ))}
            </div>
          </ChoiceField>

          <Input
            label="端点（Endpoint）"
            placeholder="https://api.example.com/v1/weather"
            error={errors.endpoint?.message}
            {...register("endpoint")}
          />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="HTTP 方法" error={errors.method?.message}>
              <select className={SELECT_CLASS} {...register("method")}>
                {HTTP_METHODS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="鉴权方式" error={errors.auth_type?.message}>
              <select className={SELECT_CLASS} {...register("auth_type")}>
                {AUTH_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {AUTH_TYPE_LABEL[value]}
                  </option>
                ))}
              </select>
            </Field>
            <Input
              label="超时（毫秒）"
              type="number"
              error={errors.timeout_ms?.message}
              {...register("timeout_ms")}
            />
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-foreground">风险等级</span>
            <input
              type="range"
              min={0}
              max={RISK_LEVELS.length - 1}
              step={1}
              value={riskIndex}
              aria-label="风险等级"
              aria-valuetext={RISK_LEVEL_LABEL[riskLevel]}
              onChange={(event) =>
                setValue("risk_level", RISK_LEVELS[Number(event.target.value)], {
                  shouldValidate: true,
                  shouldDirty: true,
                })
              }
              className="w-full accent-primary"
            />
            <div className="grid grid-cols-3 gap-1" aria-hidden="true">
              {RISK_LEVELS.map((value) => (
                <span
                  key={value}
                  data-risk-stop={value}
                  className={cn("h-1.5 rounded-full", RISK_BAND_CLASS[value])}
                />
              ))}
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              {RISK_LEVELS.map((value) => (
                <span
                  key={value}
                  className={
                    value === riskLevel ? "font-medium text-foreground" : undefined
                  }
                >
                  {RISK_LEVEL_LABEL[value]}
                </span>
              ))}
            </div>
            {errors.risk_level?.message && (
              <p className="text-sm text-danger">{errors.risk_level.message}</p>
            )}
          </div>

          <ChoiceField
            label="权限模式"
            error={errors.permission_mode?.message}
          >
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {PERMISSION_MODES.map((value) => (
                <label
                  key={value}
                  className={cn(
                    CHOICE_CARD_CLASS,
                    selectedPermissionMode === value
                      ? CHOICE_CARD_SELECTED
                      : CHOICE_CARD_IDLE,
                  )}
                >
                  <input
                    type="radio"
                    value={value}
                    className="sr-only"
                    {...register("permission_mode")}
                  />
                  {PERMISSION_MODE_LABEL[value]}
                </label>
              ))}
            </div>
          </ChoiceField>

          <div className="flex justify-end">
            <Button type="submit" loading={isSubmitting}>
              {isEdit ? "保存" : "创建"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
