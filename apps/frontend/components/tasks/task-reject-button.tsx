"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { rejectTask } from "@/lib/task-service";

export interface RejectButtonProps {
  taskId: string;
  onSuccess: () => void;
}

/** Red "拒绝" action: `pending → rejected` (caller gates by role). */
export function RejectButton({ taskId, onSuccess }: RejectButtonProps) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);

  const handleReject = async () => {
    if (!reason.trim()) return;
    setLoading(true);
    try {
      await rejectTask(taskId, reason.trim());
      toast({ type: "success", title: "已拒绝" });
      setOpen(false);
      setReason("");
      onSuccess();
    } catch (error) {
      toast({
        type: "error",
        title: "拒绝失败",
        description: extractApiErrorMessage(error),
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button
        size="sm"
        className="bg-danger text-white hover:bg-danger/90 active:bg-danger"
        onClick={() => setOpen(true)}
      >
        <X className="h-4 w-4" />
        拒绝
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="拒绝审批"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={loading}
            >
              取消
            </Button>
            <Button
              loading={loading}
              disabled={!reason.trim()}
              className="bg-danger text-white hover:bg-danger/90 active:bg-danger"
              onClick={handleReject}
            >
              确认拒绝
            </Button>
          </>
        }
      >
        <Input
          label="拒绝原因"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="请填写拒绝原因"
          maxLength={500}
        />
      </Modal>
    </>
  );
}
