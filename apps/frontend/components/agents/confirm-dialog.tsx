"use client";

import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/** Second-confirmation dialog for destructive/lifecycle actions. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = false,
  loading = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const t = useTranslations("common");

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            {t("cancel")}
          </Button>
          <Button
            onClick={onConfirm}
            loading={loading}
            className={
              danger
                ? "bg-danger text-white hover:bg-danger/90 active:bg-danger"
                : undefined
            }
          >
            {confirmLabel ?? t("confirm")}
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className={cn(
            "h-5 w-5 shrink-0",
            danger ? "text-danger" : "text-warning",
          )}
        />
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </Modal>
  );
}
