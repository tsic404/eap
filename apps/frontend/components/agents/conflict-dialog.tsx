"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

export interface ConflictDialogProps {
  open: boolean;
  onCancel: () => void;
  onReload: () => void;
}

/** Shown when an action fails with 409 (optimistic-lock version conflict). */
export function ConflictDialog({ open, onCancel, onReload }: ConflictDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="版本冲突"
      footer={
        <>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={onReload}>
            <RefreshCw className="h-4 w-4" />
            重新加载
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">
        该智能体已被他人修改，你的操作基于过期版本。请重新加载最新数据后再试。
      </p>
    </Modal>
  );
}
