"use client";

import { useState } from "react";
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { retryTask } from "@/lib/task-service";

export interface RetryButtonProps {
  taskId: string;
  onSuccess: () => void;
}

/** Blue "重试" action: `failed → executing` (caller gates by role/ownership). */
export function RetryButton({ taskId, onSuccess }: RetryButtonProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);

  const handleRetry = async () => {
    setLoading(true);
    try {
      await retryTask(taskId);
      toast({ type: "success", title: "已重试" });
      onSuccess();
    } catch (error) {
      toast({
        type: "error",
        title: "重试失败",
        description: extractApiErrorMessage(error),
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Button
      size="sm"
      loading={loading}
      className="bg-info text-white hover:bg-info/90 active:bg-info"
      onClick={handleRetry}
    >
      <RotateCcw className="h-4 w-4" />
      重试
    </Button>
  );
}
