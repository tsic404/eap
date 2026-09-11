"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Dropdown } from "@/components/ui/dropdown";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { DataTable } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { ToastProvider, useToast } from "@/components/ui/toast";
import { AdminSidebar } from "@/components/layout/admin-sidebar";
import { Header } from "@/components/layout/header";
import { UserSidebar } from "@/components/layout/user-sidebar";
import { WorkspaceSwitcher } from "@/components/layout/workspace-switcher";

interface AgentRow {
  id: string;
  name: string;
  status: "启用" | "草稿" | "停用";
  updatedAt: string;
}

const AGENT_ROWS: AgentRow[] = [
  { id: "a1", name: "客服助手", status: "启用", updatedAt: "2026-09-01" },
  { id: "a2", name: "数据分析", status: "草稿", updatedAt: "2026-08-20" },
  { id: "a3", name: "代码审查", status: "停用", updatedAt: "2026-07-15" },
  { id: "a4", name: "知识问答", status: "启用", updatedAt: "2026-09-05" },
  { id: "a5", name: "合同生成", status: "草稿", updatedAt: "2026-08-30" },
  { id: "a6", name: "翻译助手", status: "启用", updatedAt: "2026-09-08" },
];

const STATUS_VARIANT: Record<AgentRow["status"], "success" | "default" | "danger"> = {
  启用: "success",
  草稿: "default",
  停用: "danger",
};

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      {children}
    </section>
  );
}

function ToastDemo() {
  const { toast } = useToast();
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="secondary" onClick={() => toast({ type: "success", title: "保存成功" })}>
        成功
      </Button>
      <Button variant="secondary" onClick={() => toast({ type: "error", title: "操作失败", description: "请稍后重试" })}>
        错误
      </Button>
      <Button variant="secondary" onClick={() => toast({ type: "warning", title: "即将过期" })}>
        警告
      </Button>
      <Button variant="secondary" onClick={() => toast({ type: "info", title: "新版本可用" })}>
        信息
      </Button>
    </div>
  );
}

function ThrowingDemo({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error("演示用错误：渲染失败");
  }
  return <p className="text-sm text-muted-foreground">内容渲染正常。</p>;
}

function ErrorBoundaryDemo() {
  const [shouldThrow, setShouldThrow] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      <ErrorBoundary
        onError={() => {}}
        fallback={(error, reset) => (
          <div className="flex flex-col gap-2 rounded-lg border border-border p-4">
            <p className="text-sm font-medium text-foreground">捕获到错误</p>
            <p className="text-sm text-muted-foreground">{error.message}</p>
            <Button variant="outline" size="sm" onClick={reset} className="self-start">
              重试
            </Button>
          </div>
        )}
      >
        <ThrowingDemo shouldThrow={shouldThrow} />
      </ErrorBoundary>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setShouldThrow((value) => !value)}
        className="self-start"
      >
        触发错误 / 重置
      </Button>
    </div>
  );
}

function Showcase() {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-10 px-6 py-8">
      <div>
        <h1 className="text-2xl font-bold text-foreground">基础 UI 组件库</h1>
        <p className="text-sm text-muted-foreground">
          Next.js App Router + Tailwind + Design Token 组件预览
        </p>
      </div>

      <Section title="布局组件">
        <div className="flex flex-col gap-3">
          <div className="overflow-hidden rounded-lg border border-border">
            <Header />
          </div>
          <div className="flex gap-3">
            <div className="overflow-hidden rounded-lg border border-border">
              <UserSidebar active="agents" />
            </div>
            <div className="overflow-hidden rounded-lg border border-border">
              <AdminSidebar active="dashboard" />
            </div>
            <div className="flex items-start p-4">
              <WorkspaceSwitcher />
            </div>
          </div>
        </div>
      </Section>

      <Section title="Button">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary">主要</Button>
          <Button variant="secondary">次要</Button>
          <Button variant="outline">描边</Button>
          <Button variant="ghost">幽灵</Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm">小</Button>
          <Button size="md">中</Button>
          <Button size="lg">大</Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button loading>加载中</Button>
          <Button disabled>禁用</Button>
        </div>
      </Section>

      <Section title="Input">
        <div className="grid max-w-md grid-cols-1 gap-4">
          <Input label="名称" placeholder="请输入名称" />
          <Input label="邮箱" error="邮箱格式不正确" defaultValue="invalid" />
          <Input label="描述" helperText="最多 200 字" placeholder="请输入描述" />
        </div>
      </Section>

      <Section title="Badge">
        <div className="flex flex-wrap gap-2">
          <Badge>默认</Badge>
          <Badge variant="success">成功</Badge>
          <Badge variant="warning">警告</Badge>
          <Badge variant="danger">危险</Badge>
          <Badge variant="info">信息</Badge>
        </div>
      </Section>

      <Section title="Card">
        <div className="grid max-w-3xl grid-cols-3 gap-4">
          {(["default", "hover", "interactive"] as const).map((variant) => (
            <Card key={variant} variant={variant}>
              <CardHeader>
                <CardTitle>{variant}</CardTitle>
                <CardDescription>卡片描述文本</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">卡片内容区域。</p>
              </CardContent>
              <CardFooter>
                <Button variant="outline" size="sm">操作</Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      </Section>

      <Section title="Modal">
        <Button onClick={() => setModalOpen(true)}>打开弹窗</Button>
        <Modal
          open={modalOpen}
          onClose={() => setModalOpen(false)}
          title="确认操作"
          footer={
            <>
              <Button variant="outline" onClick={() => setModalOpen(false)}>
                取消
              </Button>
              <Button onClick={() => setModalOpen(false)}>确认</Button>
            </>
          }
        >
          <p className="text-sm text-muted-foreground">
            弹窗内容。按 Esc、点击遮罩或右上角按钮关闭。
          </p>
        </Modal>
      </Section>

      <Section title="Tabs">
        <Tabs
          items={[
            { value: "overview", label: "概览", content: <p className="text-sm">概览内容</p> },
            { value: "config", label: "配置", content: <p className="text-sm">配置内容</p> },
            { value: "logs", label: "日志", content: <p className="text-sm">日志内容</p> },
          ]}
        />
      </Section>

      <Section title="Dropdown">
        <Dropdown
          align="left"
          triggerClassName="rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-muted"
          trigger="打开菜单"
          items={[
            { value: "edit", label: "编辑" },
            { value: "duplicate", label: "复制" },
            { value: "delete", label: "删除", danger: true },
          ]}
        />
      </Section>

      <Section title="Table">
        <DataTable
          selectable
          pageSize={3}
          rowKey={(row) => row.id}
          emptyTitle="暂无智能体"
          emptyDescription="创建第一个智能体开始使用"
          columns={[
            {
              key: "name",
              header: "名称",
              sortable: true,
              sortValue: (row) => row.name,
              cell: (row) => <span className="font-medium">{row.name}</span>,
            },
            {
              key: "status",
              header: "状态",
              sortable: true,
              sortValue: (row) => row.status,
              cell: (row) => <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>,
            },
            { key: "updatedAt", header: "更新时间", cell: (row) => row.updatedAt },
          ]}
          data={AGENT_ROWS}
        />
      </Section>

      <Section title="Toast">
        <ToastDemo />
      </Section>

      <Section title="Skeleton / Spinner / EmptyState">
        <div className="grid max-w-3xl grid-cols-3 gap-4">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-8 w-full" />
          </div>
          <div className="flex items-center gap-4">
            <Spinner size="sm" />
            <Spinner size="md" />
            <Spinner size="lg" />
          </div>
          <EmptyState
            title="暂无数据"
            description="这里还没有内容"
            action={<Button variant="outline" size="sm">创建</Button>}
          />
        </div>
      </Section>

      <Section title="ErrorBoundary">
        <ErrorBoundaryDemo />
      </Section>
    </div>
  );
}

export default function ComponentDemoPage() {
  return (
    <ToastProvider>
      <Showcase />
    </ToastProvider>
  );
}
