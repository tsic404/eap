"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
  loading?: boolean;
  /**
   * Name the user must retype to confirm. The action stays disabled until the
   * typed value matches after trimming, case-sensitively (C-49).
   */
  requiredName?: string;
  /** Extra detail below the description, e.g. the affected conversations. */
  children?: React.ReactNode;
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
  requiredName,
  children,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const t = useTranslations("common");
  const [typedName, setTypedName] = useState("");

  // Each opening starts from an empty field: a cancelled attempt must not leave
  // a previously matching name behind (C-49).
  useEffect(() => {
    if (!open) setTypedName("");
  }, [open]);

  const nameMatches =
    requiredName === undefined || typedName.trim() === requiredName.trim();

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
            disabled={!nameMatches}
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
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <AlertTriangle
            className={cn(
              "h-5 w-5 shrink-0",
              danger ? "text-danger" : "text-warning",
            )}
          />
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {children}
        {requiredName !== undefined && (
          <Input
            label={`请输入「${requiredName}」以确认`}
            value={typedName}
            onChange={(event) => setTypedName(event.target.value)}
            disabled={loading}
          />
        )}
      </div>
    </Modal>
  );
}
