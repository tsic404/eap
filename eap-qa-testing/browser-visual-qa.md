# 浏览器渲染测试用例（Browser Visual QA）

本文档覆盖 `eap-qa-testing` 技能的**浏览器渲染验收场景**：通过真实浏览器（Playwright 或手动 Chrome/Edge）访问 `http://localhost:3000`，验证用户可见的 UI 渲染、交互行为与响应式布局，并逐条标注截图证据要求。

- 编号约定：`BC01`–`BC08` 为场景，`BC0X-N` 为子用例。
- 截图约定：标注「截图：…」的用例须产出截图作为验收证据；标注「截图：不需要」的用例仅做代码 / DOM / HTTP 检查。
- 代码对应：每个场景标注其覆盖的仓库文件（路径相对仓库根，前端代码位于 `apps/frontend/`）。

## 执行前提

| 项 | 要求 |
|---|---|
| 前端 | 仓库根执行 `pnpm --filter @eap/frontend dev`，监听 `http://localhost:3000` |
| 浏览器 | Playwright（Chromium）或手动 Chrome/Edge 最新稳定版 |
| 全栈（BC07/BC08） | 仓库根执行 `docker compose up -d`，nginx 监听 `http://localhost`（默认 80 端口） |
| 环境变量 | 全栈启动前须准备 `.env`（`DIFY_SECRET_KEY`、`WEAVIATE_API_KEY` 必填，缺失时 compose 快速失败） |

## 截图要求约定

- 默认全页截图，viewport 1280×800（BC04 除外，按各断点明确指定）。
- 交互状态截图（Modal / Toast / Dropdown 展开）须在对应状态可见时抓取。
- 截图文件建议命名 `bcXX-N_描述.png`，随验收结果一并提交。

---

## BC01 首页渲染

覆盖代码：`apps/frontend/app/page.tsx`

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC01-1 | 首页内容渲染 | 前端 dev server 已启动 | 浏览器访问 `http://localhost:3000/` | 页面垂直/水平居中显示三块内容：H1「EAP — 企业智能体平台」；副标题「Enterprise Agent Platform」（`text-muted-foreground` 灰色）；其下一个 primary 按钮（`bg-primary` 蓝色背景 + 白色前景）文案「查看基础 UI 组件库」 | 截图：首页全屏 |
| BC01-2 | 跳转组件预览页 | 已在首页 | 点击「查看基础 UI 组件库」链接（代码为 `<a href="/components">`，视觉为按钮） | 跳转到 `/components`，渲染组件预览页（见 BC02） | 截图：跳转后的 `/components` 首屏 |

---

## BC02 组件预览页渲染

覆盖代码：`apps/frontend/app/components/page.tsx` 及其引用的 `apps/frontend/components/**` 组件。

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC02-1 | 页面标题渲染 | 前端 dev server 已启动 | 浏览器访问 `http://localhost:3000/components` | 顶部显示 H1「基础 UI 组件库」+ 副标题「Next.js App Router + Tailwind + Design Token 组件预览」 | 截图：页面首屏 |
| BC02-2 | 布局组件 section | 同上 | 滚动到「布局组件」section 观察 | Header 顶部栏（左侧「EAP」标识 + WorkspaceSwitcher；右侧通知铃铛按钮 `aria-label="通知"` + 圆形头像「管」）；UserSidebar 5 导航项（会话 / 智能体 / 知识库 / 工具 / 任务中心）；AdminSidebar 6 导航项（仪表盘 / 用户管理 / 智能体审核 / 模型网关 / 审计日志 / 系统设置）；WorkspaceSwitcher 独立展示 | 截图：布局组件 section |
| BC02-3 | Button section | 同上 | 滚动到「Button」section 观察 | 4 变体：主要（primary 蓝底）/ 次要（secondary 灰底）/ 描边（outline）/ 幽灵（ghost 透明）；3 尺寸：小 sm / 中 md / 大 lg；loading 态「加载中」（内嵌 Spinner）；disabled 态「禁用」（50% 透明度灰显、不可点击） | 截图：Button section |
| BC02-4 | Input section | 同上 | 滚动到「Input」section 观察 | 名称输入框（label「名称」+ placeholder「请输入名称」）；邮箱输入框（label「邮箱」+ 红色错误提示「邮箱格式不正确」）；描述输入框（label「描述」+ 灰色 helperText「最多 200 字」） | 截图：Input section |
| BC02-5 | Badge section | 同上 | 滚动到「Badge」section 观察 | 5 变体：默认（灰）/ 成功 success（绿）/ 警告 warning（橙）/ 危险 danger（红）/ 信息 info（蓝），圆形胶囊样式 | 截图：Badge section |
| BC02-6 | Card section | 同上 | 滚动到「Card」section，分别观察并悬停 3 张卡片 | 3 变体三列并排：default（`shadow-sm`）/ hover（悬停阴影变 `shadow-md`）/ interactive（悬停边框变 primary 蓝 + 阴影加深） | 截图：Card section（含 interactive 悬停态） |
| BC02-7 | Modal 打开 | 同上 | 滚动到「Modal」section，点击「打开弹窗」按钮 | 弹出弹窗：背景遮罩 fade-in（`bg-black/40`）+ 面板 scale-in（`animate-scale-in`），标题「确认操作」+ 内容文案 + 底部「取消」「确认」两按钮 | 截图：Modal 打开状态 |
| BC02-8 | Modal 关闭 | Modal 已打开 | 分别用三种方式关闭：按 Esc 键 / 点击遮罩 / 点击右上角 X 按钮（`aria-label="关闭"`） | 三种方式均关闭弹窗，页面恢复、body 滚动锁解除 | 截图：不需要（观察关闭行为） |
| BC02-9 | Tabs section | 组件预览页已加载 | 滚动到「Tabs」section，依次点击 3 个标签 | 3 标签页：概览 / 配置 / 日志；点击后激活态（primary 蓝色下边框 + 文字变蓝），内容区切换显示对应文本（概览内容 / 配置内容 / 日志内容） | 截图：Tabs 切换后的激活态 |
| BC02-10 | Dropdown section | 同上 | 滚动到「Dropdown」section，点击「打开菜单」 | 下拉菜单显示 3 项：编辑 / 复制 / 删除（删除为 danger 红色） | 截图：Dropdown 展开状态 |
| BC02-11 | Table section | 同上 | 滚动到「Table」section 观察 | 6 行智能体数据（客服助手 / 数据分析 / 代码审查 / 知识问答 / 合同生成 / 翻译助手）；「名称」「状态」列可排序（点击表头切换升/降序）；状态列 Badge 变色（启用=绿 / 草稿=灰 / 停用=红）；分页 3 行/页（6 行分 2 页，含上一页/下一页）；每行及表头可勾选 | 截图：Table section（含排序态） |
| BC02-12 | Toast section | 同上 | 滚动到「Toast」section，点击「成功」按钮；再点击「错误」按钮 | 右上角（`fixed right-4 top-4`）弹出绿色成功 Toast「保存成功」（`role="status"`，`animate-slide-in-right` 滑入，约 5s 自动消失）；「错误」弹出红色错误 Toast「操作失败 / 请稍后重试」。代码另含「警告」「信息」两按钮 | 截图：Toast 弹出状态（成功 + 错误各一张） |
| BC02-13 | Skeleton / Spinner / EmptyState | 同上 | 滚动到「Skeleton / Spinner / EmptyState」section 观察 | 骨架屏（3 条 `animate-pulse` 灰块脉动动画）；3 尺寸 Spinner（sm / md / lg 旋转动画）；空状态（收件箱图标 + 「暂无数据」+「这里还没有内容」+「创建」按钮） | 截图：该 section |
| BC02-14 | ErrorBoundary section | 同上 | 滚动到「ErrorBoundary」section，点击「触发错误 / 重置」按钮 | 错误捕获区域显示「捕获到错误」+ 错误信息「演示用错误：渲染失败」+「重试」按钮（原「内容渲染正常」被替换，页面其余部分不受影响）；点击「重试」恢复正常内容 | 截图：错误捕获状态 |

---

## BC03 工作区切换交互

覆盖代码：`apps/frontend/components/layout/workspace-switcher.tsx`

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC03-1 | 默认显示 | 组件预览页已加载（Header 或「布局组件」section 内的 WorkspaceSwitcher） | 观察切换器初始状态 | 切换器 trigger 默认显示「用户工作区」+ ChevronsUpDown 图标 | 截图：切换器初始态 |
| BC03-2 | 展开下拉 | 同上 | 点击切换器 trigger | 下拉菜单显示 2 项：用户工作区 / 管理工作区 | 截图：下拉展开状态 |
| BC03-3 | 选择切换 | 下拉已展开 | 点击「管理工作区」 | 菜单关闭，trigger 文本变为「管理工作区」 | 截图：切换后的 trigger |
| BC03-4 | onChange 回调 | 同上 | 选择任意工作区后，观察内部 state 更新（非受控模式） | 组件内部 state 更新（`setInternalValue`），并触发 `onChange` 回调；受控/非受控两种模式行为一致 | 截图：不需要（行为观察） |

---

## BC04 响应式布局

覆盖代码：`apps/frontend/app/components/page.tsx`；断点策略参照架构文档 §30.10（Mobile < 768px、Tablet 768–1024px、Desktop > 1024px）。

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC04-1 | 桌面端（1920px） | 前端 dev server 已启动 | viewport 设为 1920×1080，访问 `/components` | 页面内容 `max-w-5xl`（64rem）居中，网格布局正常，无横向滚动条 | 截图：1920px 全页 |
| BC04-2 | 平板端（768px） | 同上 | viewport 设为 768×1024，访问 `/components` | 页面间距与网格适配平板宽度，Card 等网格仍可用，无内容溢出 | 截图：768px 全页 |
| BC04-3 | 移动端（375px） | 同上 | viewport 设为 375×812，访问 `/components` | 页面内容堆叠（单列），Card 三列网格（`grid-cols-3`）在移动断点降为单列展示，布局组件区不再横向溢出 | 截图：375px 全页 |

> 注：若当前实现（`app/components/page.tsx` 中 `grid-cols-3` 等固定网格）未随断点降级，本用例判为 FAIL，即暴露响应式缺口，作为 QA 结论输出。

---

## BC05 暗色模式预留

覆盖代码：`apps/frontend/app/globals.css`、`apps/frontend/tailwind.config.ts`；机制参照架构文档 §30.13。

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC05-1 | `:root` 变量完整 | 可访问仓库源码 | 代码检查 `globals.css` 的 `:root` 块 | `:root` 中定义完整 Design Token（brand / neutral / semantic / radius / shadow 各分组）；不通过 `@media (prefers-color-scheme)` 自动切换；暗色通过 `.dark` 类（Tailwind `dark:` 前缀）预留切换 | 截图：不需要（仅代码检查） |
| BC05-2 | 语义类引用变量 | 同上 | 代码检查 `components/**` 与 `tailwind.config.ts` | 所有组件颜色通过 Tailwind 语义类（`bg-background` / `text-foreground` / `bg-primary` / `text-muted-foreground` 等）引用 CSS 变量，不硬编码 hex；Tailwind 主题将 token 映射到 `var(--color-*)` | 截图：不需要（仅代码检查） |

---

## BC06 可访问性

覆盖代码：各组件；规范参照架构文档 §30.9（语义化 HTML + 键盘导航 + WCAG AA）。

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC06-1 | 首页 H1 | 前端 dev server 已启动 | DOM 检查首页 `/` | 首页存在 `<h1>` 且内容包含「企业智能体平台」 | 截图：不需要（DOM 检查） |
| BC06-2 | Button focus 环 | 首页已加载 | Tab 键聚焦「查看基础 UI 组件库」按钮 | 按钮出现 `focus-visible:ring-2 ring-ring ring-offset-2` 可见焦点环 | 截图：焦点环可见态 |
| BC06-3 | Input label 关联 | 组件预览页已加载 | DOM 检查 Input section | 每个 `<input>` 的 `id` 与 `<label htmlFor>` 一致关联（代码用 `useId()` 生成） | 截图：不需要（DOM 检查） |
| BC06-4 | Input error 语义 | 同上 | DOM 检查邮箱输入框（error 态） | 邮箱输入框带 `aria-invalid="true"` + `aria-describedby`（指向错误提示 `<p>`） | 截图：不需要（DOM 检查） |
| BC06-5 | Modal 语义 | Modal 已打开 | DOM 检查弹窗 | 弹窗容器带 `role="dialog"` + `aria-modal="true"` | 截图：不需要（DOM 检查） |
| BC06-6 | Tabs 语义 | 组件预览页已加载 | DOM 检查 Tabs section | 容器 `role="tablist"`，标签按钮 `role="tab"` + `aria-selected`，面板 `role="tabpanel"` | 截图：不需要（DOM 检查） |
| BC06-7 | Table checkbox aria-label | 同上 | DOM 检查 Table section | 表头勾选框带 `aria-label="选择当前页全部"`，每行勾选框带 `aria-label="选择行"` | 截图：不需要（DOM 检查） |
| BC06-8 | 色彩对比度 | 同上 | 计算 primary 蓝（`#2563eb`）+ 白色前景对比度 | 对比度 ≥ WCAG AA 4.5:1（实测约 5.17:1）；Badge 等彩色文字使用 `*-subtle` 浅色底 + 语义色文字配对，避免深色文字直铺白色背景 | 截图：不需要（自动化对比度检查） |

---

## BC07 全栈 Docker Compose 验证

覆盖代码：`docker-compose.yml`（8 个服务：nginx / frontend / backend / dify-api / dify-worker / postgres / redis / weaviate）。

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC07-1 | 8 容器启动 | 已准备 `.env` | 仓库根执行 `docker compose up -d` 并等待健康检查通过 | 8 个容器（nginx / frontend / backend / dify-api / dify-worker / postgres / redis / weaviate）全部 running 且 healthy | 截图：不需要（`docker compose ps` 输出） |
| BC07-2 | nginx liveness | 全栈已启动 | `curl -i http://localhost/healthz` | 返回 HTTP 200，body 为 `ok`（`location = /healthz`） | 截图：不需要（curl 输出） |
| BC07-3 | 后端代理链 | 同上 | `curl -i http://localhost/api/health/live` | 返回 HTTP 200，body 为 `{"status":"ok"}`（nginx → backend:3001 代理链） | 截图：不需要（curl 输出） |
| BC07-4 | nginx 首页渲染 | 同上 | 浏览器访问 `http://localhost/` | 首页正常渲染（nginx → frontend:3000 代理链），内容同 BC01-1 | 截图：浏览器经 nginx 访问首页 |
| BC07-5 | nginx 组件页渲染 | 同上 | 浏览器访问 `http://localhost/components` | 组件预览页正常渲染（同 BC02） | 截图：浏览器经 nginx 访问 `/components` |

---

## BC08 Nginx 反向代理验证

覆盖代码：`deploy/nginx/nginx.conf`

| # | 用例 | 前置条件 | 操作步骤 | 期望渲染效果 | 截图要求 |
|---|---|---|---|---|---|
| BC08-1 | `/api/` 代理到 backend | 全栈已启动 | `curl -i http://localhost/api/health/live`，检查响应头与上游 | 请求代理到 `backend:3001`；透传 `X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto` 头 | 截图：不需要（HTTP 头检查） |
| BC08-2 | 非 `/api/` 代理到 frontend | 同上 | `curl -i http://localhost/`，检查代理目标与头 | 请求代理到 `frontend:3000`；透传 `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto`，并携带 `Upgrade` / `Connection: "upgrade"`（WebSocket 预留） | 截图：不需要（HTTP 头检查） |
| BC08-3 | `/healthz` 不走代理 | 同上 | `curl -i http://localhost/healthz` | nginx 自身直接返回 200 `ok`，不转发到任何上游（`location = /healthz`） | 截图：不需要（curl 输出） |
