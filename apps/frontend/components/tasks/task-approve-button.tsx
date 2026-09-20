"use client";

import { useState } from "react";
import { Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { approveTask } from "@/lib/task-service";

export interface ApproveButtonProps {
  taskId: string;
  onSuccess: () => void;
}

/** Green "同意" action: `pending → approved` (caller gates by role). */
export function ApproveButton({ taskId, onSuccess }: ApproveButtonProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);

  const handleApprove = async () => {
    setLoading(true);
    try {
      await approveTask(taskId);
      toast({ type: "success", title: "已通过" });
      onSuccess();
    } catch (error) {
      toast({
        type: "error",
        title: "审批失败",
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
      className="bg-success text-white hover:bg-success/90 active:bg-success"
      onClick={handleApprove}
    >
      <Check className="h-4 w-4" />
      同意
    </Button>
  );
}
