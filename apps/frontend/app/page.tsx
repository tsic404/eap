export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <h1 className="text-3xl font-bold text-foreground">
        EAP — 企业智能体平台
      </h1>
      <p className="text-muted-foreground">Enterprise Agent Platform</p>
      <a
        href="/components"
        className="mt-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary-hover"
      >
        查看基础 UI 组件库
      </a>
    </main>
  );
}
