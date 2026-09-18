"use client";

import { useState } from "react";
import { Play } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { debugTool } from "@/lib/tool-service";
import type { DebugToolResult } from "@/lib/tool-types";

const EXAMPLE_PARAMS = '{\n  "city": "北京"\n}';

function formatResponse(body: unknown): string {
  if (typeof body === "string") return body;
  return JSON.stringify(body, null, 2);
}

export interface DebugPanelProps {
  toolId: string;
  disabled?: boolean;
}

/** Online-debug panel: JSON body → send → status/latency/response. */
export function DebugPanel({ toolId, disabled = false }: DebugPanelProps) {
  const { toast } = useToast();
  const [body, setBody] = useState(EXAMPLE_PARAMS);
  const [saveCase, setSaveCase] = useState(false);
  const [caseName, setCaseName] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DebugToolResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const parseBody = (): Record<string, unknown> | null => {
    if (body.trim() === "") return {};
    try {
      const parsed: unknown = JSON.parse(body);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      return null;
    }
  };

  const run = async () => {
    const params = parseBody();
    if (params === null) {
      setError("请求体必须是合法的 JSON 对象");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const data = await debugTool(toolId, {
        params,
        save_as_test_case: saveCase,
        name: saveCase && caseName.trim() !== "" ? caseName.trim() : null,
      });
      setResult(data);
      if (data.savedCaseId) {
        toast({ type: "success", title: "用例已保存" });
      }
    } catch (runError) {
      setError(extractApiErrorMessage(runError));
      setResult(null);
    } finally {
      setRunning(false);
    }
  };

  const statusVariant =
    result && result.statusCode >= 200 && result.statusCode < 300
      ? "success"
      : "danger";

  return (
    <Card>
      <CardHeader>
        <CardTitle>调试面板</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="debug-body"
            className="text-sm font-medium text-foreground"
          >
            请求体（JSON）
          </label>
          <textarea
            id="debug-body"
            rows={6}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            disabled={disabled}
            placeholder={EXAMPLE_PARAMS}
            className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={saveCase}
              onChange={(event) => setSaveCase(event.target.checked)}
              disabled={disabled}
              className="accent-primary"
            />
            保存为用例
          </label>
          {saveCase && (
            <input
              type="text"
              value={caseName}
              onChange={(event) => setCaseName(event.target.value)}
              disabled={disabled}
              placeholder="用例名称（可选）"
              className="h-9 w-56 rounded-md border border-border bg-background px-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
            />
          )}
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={run} loading={running} disabled={disabled}>
            <Play className="h-4 w-4" />
            发送请求
          </Button>
        </div>

        {error && (
          <p className="rounded-md border border-border bg-danger-subtle p-3 text-sm text-danger">
            {error}
          </p>
        )}

        {result && (
          <div className="flex flex-col gap-2 rounded-md border border-border bg-muted p-3">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge variant={statusVariant}>{result.statusCode}</Badge>
              <span className="text-muted-foreground">
                耗时 {result.latencyMs.toFixed(0)} ms
              </span>
              {result.savedCaseId && (
                <span className="text-muted-foreground">已保存用例</span>
              )}
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap text-sm text-foreground">
              {formatResponse(result.responseBody)}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
