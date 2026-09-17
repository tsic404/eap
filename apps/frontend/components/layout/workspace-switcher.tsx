"use client";

import { useState } from "react";

import { ChevronsUpDown } from "lucide-react";

import { Dropdown } from "@/components/ui/dropdown";
import { canAccessAdmin, type UserRole } from "@/lib/roles";

const WORKSPACES = [
  { value: "user", label: "用户工作区" },
  { value: "admin", label: "管理工作区" },
] as const;

export interface WorkspaceSwitcherProps {
  /** Current user's role; employee sees the user workspace only. */
  role?: UserRole;
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
}

/** Switches between the user and admin workspaces. */
export function WorkspaceSwitcher({
  role = "employee",
  value,
  defaultValue = "user",
  onChange,
}: WorkspaceSwitcherProps) {
  const workspaces = WORKSPACES.filter(
    (workspace) => canAccessAdmin(role) || workspace.value === "user",
  );
  const [internalValue, setInternalValue] = useState(defaultValue);
  const isControlled = value !== undefined;
  const currentValue = isControlled ? value : internalValue;
  const current =
    workspaces.find((workspace) => workspace.value === currentValue) ??
    workspaces[0];

  const select = (nextValue: string) => {
    if (!isControlled) setInternalValue(nextValue);
    onChange?.(nextValue);
  };

  return (
    <Dropdown
      align="left"
      triggerClassName="gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-foreground hover:bg-muted"
      trigger={
        <>
          {current.label}
          <ChevronsUpDown className="h-4 w-4 text-muted-foreground" />
        </>
      }
      items={workspaces.map((workspace) => ({
        value: workspace.value,
        label: workspace.label,
        onSelect: () => select(workspace.value),
      }))}
    />
  );
}
