---
name: eap-qa-testing
description: 企业智能体平台（EAP）QA 验收测试技能。QA agent 验收 EAP 仓库的任何模块（后端 FastAPI、前端 Next.js、浏览器渲染）时使用本技能获取可执行、可判定的测试用例集，覆盖构建门、API 契约、中间件管道、UI 组件规范、Design Token 一致性、浏览器渲染效果等场景。
---

# eap-qa-testing — EAP QA 验收测试技能

## 1. 技能总览

本技能是 EAP 仓库的 QA 验收入口：主文件给出从零开始的整体验收流程与跨模块快速索引，三个子文件提供各模块可执行、可判定的用例集。QA agent 验收任意变更时，先按 `## 3. 整体测试步骤` 走完整流程，再按 `## 4. 快速索引表` 定位到具体模块用例。

### 1.1 文件结构表

| 文件 | 覆盖范围 | 用例编号 | 产出状态 |
|------|---------|---------|---------|
| `SKILL.md`（本文件） | 整体测试步骤、构建门、测试金字塔、快速索引、数据策略、Dify 分层 | — | 本任务 |
| `backend-qa.md` | 后端 FastAPI：健康检查、配置、中间件管道、统一响应格式、异常处理、数据模型、Seed、日志 | SC01–SC08 | 本任务 |
| `frontend-qa.md` | 前端 Next.js：UI 组件规范、Design Token 一致性（架构文档 §30.13）、组件单测、hooks | FCxx | 并行任务产出（仅引用，不等待） |
| `browser-visual-qa.md` | 浏览器渲染：E2E 用户旅程、视觉一致性、响应式、a11y | BC01–BC08 | 并行任务产出（仅引用，不等待） |

### 1.2 适用场景

- **验收评审**：按整体步骤全量验收，产出分步骤通过/失败结论。
- **缺陷回归**：按快速索引表只跑与变更相关的用例编号，验证修复。
- **提交门**：任何 PR 至少满足 Step 2 构建门 + 受影响模块的用例集。
- **专项审计**：安全（§31.1.6）、性能（§2）、a11y（§2）可独立抽测。

## 2. 测试金字塔

依据架构文档 §31.1.1，测试分层与占比：

| 层 | 占比 | 工具 | 执行频率 | 责任人 |
|----|------|------|---------|--------|
| E2E 用户旅程 | 5% | Playwright | 每次 PR | FE + QA |
| API 集成测试 | 25% | httpx.AsyncClient + testcontainers | 每次 PR | BE |
| 单元测试 | 65% | pytest (BE) + Vitest (FE) | 每次 PR | FE + BE |
| 性能测试 | 5% | k6 / Artillery | 每次 P1 里程碑 | QA |
| 安全扫描 | — | OWASP ZAP / Trivy | 每次 P 阶段结束 | DevOps |
| a11y 检查 | — | axe-core / pa11y | 每次 PR | CI 自动 |

对应子文件分工：单元 + API 集成 → `backend-qa.md`（SC 编号）与 `frontend-qa.md`（FC 编号）；E2E + a11y + 视觉 → `browser-visual-qa.md`（BC 编号）。

## 3. 整体测试步骤

从零开始的验收流程。每一步有明确通过判据；任一步失败即停止并记录，不进入下一步。

### Step 1: 环境准备

- 复制环境变量：`cp .env.example .env`，填入 `DIFY_SECRET_KEY`、`WEAVIATE_API_KEY` 等必填项（compose 使用 `${VAR:?...}`，缺值即 fail-fast 拒绝启动）。
- 启动全栈：`docker compose up -d`。
- 等待健康：8 个容器 —— `nginx`、`frontend`、`backend`、`dify-api`、`dify-worker`、`postgres`、`redis`、`weaviate`。

通过判据：

- `docker compose ps` 全部容器 `Status` 为 `healthy`。
- `curl http://localhost/api/health/live` 返回 200 + `{"status":"ok"}`（对应 `backend-qa.md` SC01-2）。

### Step 2: 构建门

前端（workspace 根目录）：

```bash
pnpm -r lint && pnpm -r typecheck && pnpm -r test && pnpm -r build
```

后端（`apps/backend/`）：

```bash
ruff check . && mypy app && pytest
```

通过判据：

- 所有命令退出码为 0；`pytest` 输出 `N passed, 0 failed`。
- 前端单测走 Vitest（`apps/frontend` 的 `test` script）、后端走 pytest；禁止用脚本替代（见 `## 7. 禁止事项`）。

### Step 3: 后端 API 测试

- 读取 `backend-qa.md`，按 SC01–SC08 逐条执行。
- 契约/单元类用例（SC02/SC04/SC05/SC06/SC08 及 SC03 中直接调用中间件方法的用例）不依赖外部服务，可直接跑。
- 走完整 HTTP 管道的用例用 FastAPI `TestClient`（`tests/conftest.py::client`）。
- 涉及真实依赖的用例（SC01 ready 探针、SC07 Seed 入库）需全栈或 testcontainers（§31.1.3 集成层）。

通过判据：每条用例的 `期望` 列断言成立；`对应代码` 路径与实际文件、函数名一致。

### Step 4: 前端组件测试

- 读取 `frontend-qa.md`，按 FC 编号逐条执行。
- 覆盖：UI 组件规范、Design Token 一致性（§30.13）、组件单测、hooks。
- 该文件由并行任务产出；若尚未就绪，标记"待验证"，不阻塞后端验收。

### Step 5: 浏览器渲染测试

- 读取 `browser-visual-qa.md`，按 BC 编号执行。
- 覆盖：E2E 用户旅程（§31.2 的 8 个 P0 场景）、视觉一致性、响应式、a11y（axe-core）。
- 该文件由并行任务产出；若尚未就绪，标记"待验证"。

### Step 6: 集成测试

- 前置：Docker 全栈已启动（Step 1）且构建门通过（Step 2）。
- 全栈联调：经 `nginx`（`http://localhost`）走通前后端链路。
- API 联调：命中真实 postgres/redis/dify，验证数据写入（Seed 幂等，见 SC07-4）与探针（`/api/health/ready` 三项 `ok`，见 SC01-3）。

通过判据：端到端请求经 nginx → 后端 → 依赖返回正确；无 5xx、无 CORS/限流误报（对照 SC03-2/SC03-3/SC03-7）。

## 4. 快速索引表

按问题类型定位到子文件的具体用例编号。

| 问题类型 | 子文件 | 用例编号 | 定位关键词 |
|---------|--------|---------|-----------|
| 构建门（lint/typecheck/test/build） | 本文件 | Step 2 | 构建门 |
| 健康检查端点异常 | `backend-qa.md` | SC01-1 … SC01-4 | health |
| 配置/环境变量错误 | `backend-qa.md` | SC02-1 … SC02-4 | config |
| 中间件管道/安全头 | `backend-qa.md` | SC03-1 | helmet |
| CORS 白名单/拒绝/preflight | `backend-qa.md` | SC03-2 … SC03-4 | CORS |
| 请求 ID 生成/透传 | `backend-qa.md` | SC03-5 … SC03-6 | request-id |
| 速率限制/X-Forwarded-For | `backend-qa.md` | SC03-7 … SC03-10 | rate limit |
| JWT 鉴权/identity 注入 | `backend-qa.md` | SC03-11 … SC03-12 | JWT |
| 流式响应 | `backend-qa.md` | SC03-13 | streaming |
| 响应格式（data envelope） | `backend-qa.md` | SC04-1 … SC04-4 | transform |
| 异常/错误码不符 | `backend-qa.md` | SC05-1 … SC05-3 | errors |
| 数据模型/约束/索引/外键 | `backend-qa.md` | SC06-1 … SC06-8 | models |
| Seed/幂等/并发 | `backend-qa.md` | SC07-1 … SC07-5 | seed |
| 日志/脱敏/上下文 | `backend-qa.md` | SC08-1 … SC08-3 | logging |
| UI 组件/Design Token/组件单测 | `frontend-qa.md` | FCxx | 组件、token |
| 浏览器渲染/E2E/视觉/a11y | `browser-visual-qa.md` | BC01–BC08 | 渲染、E2E |
| 性能 | 本文件 §2 | — | 性能测试 5% |
| 安全 | 本文件 §6 + 架构 §31.1.6 | — | 安全扫描 |

## 5. 测试数据策略

依据架构文档 §31.1.4：

- **Seed 脚本**：`app/seed.py` 创建默认租户（slug=`default`）+ platform_admin 用户（`admin@example.com`）+ 预置全局工具模板（`tool-template-http` / `tool-template-webhook`，`tenant_id IS NULL`）。幂等、自愈，并发由 `pg_advisory_xact_lock` 序列化（见 SC07）。
- **工厂函数**：构造测试数据用 `create_test_agent(tenant_id, **overrides)` / `create_test_kb(tenant_id, **overrides)` / `create_test_conversation(user_id, agent_id)` 模式。
- **租户隔离断言**：`assert tenant_b_agent_id not in [a.id for a in tenant_a_response.data]` 模式，验证跨租户不可见（配合 SC06-5 租户索引）。

## 6. Dify 依赖分层

依据架构文档 §31.1.3：

| 测试层 | Dify 处理方式 | 适用场景 |
|--------|-------------|---------|
| 单元测试 | Mock `DifyClientService` 和 `DifyConsoleClient`（固定 JSON fixture） | Router/Service/Repository/Adapter 快速验证 |
| 集成测试 | 真实 Dify 容器（testcontainers 或 docker-compose 测试 profile） | SSE 流式解析、KB 上传→索引→召回全链路 |
| E2E | staging 环境真实 Dify（预置 seed App 和 Dataset） | 登录→创建 Agent→对话→Trace 完整旅程 |

安全与迁移测试（架构 §31.1.6）：OWASP ZAP baseline scan（P 阶段结束）；迁移回滚验证 `alembic upgrade head → seed.py → downgrade -1 → upgrade head → seed.py`；a11y 用 `@axe-core/playwright` 拦截 color-contrast 违规。

## 7. 架构文档交叉引用

本技能引用的架构文档章节与对应仓库位置：

| 架构章节 | 主题 | 对应实现/引用位置 |
|---------|------|------------------|
| §4.2 | 请求生命周期（中间件管道） | `apps/backend/app/main.py::create_app` · `app/middleware/` |
| §16.1 / §16.2 | 前端技术栈与目录结构 | `apps/frontend/` · `apps/frontend/src/` |
| §25 | 可观测性设计（健康检查/日志） | `app/health.py` · `app/logging_conf.py` |
| §30.13 | Design Token 体系 | `frontend-qa.md`（FC 用例） |
| §31.1.1 | 测试金字塔 | 本文件 §2 |
| §31.1.3 | Dify 依赖分层 | 本文件 §6 |
| §31.1.4 | 测试数据策略 | 本文件 §5 |
| §31.1.6 | 安全与迁移测试 | 本文件 §6 |

## 8. 验收输出模板

QA 验收完成后，按以下结构输出结论（每条绑定用例编号）：

```text
模块/步骤        | 结论    | 证据（用例编号 + 断言结果）
-----------------|---------|-------------------------------
Step 2 构建门     | 通过/失败 | ruff/mypy/pytest/pnpm 退出码与通过数
backend SC01-08  | 通过/失败 | 逐条 SC0X-N 断言结果
frontend FCxx    | 通过/失败/待验证 | FC 编号断言结果
browser BCxx     | 通过/失败/待验证 | BC 编号断言结果
Step 6 集成      | 通过/失败 | 全栈联调结果
```

规则：每个"失败"必须附带失败用例编号、实际输出与期望输出；"待验证"仅允许出现在并行任务未就绪的子文件上，且需说明原因。

## 9. 禁止事项

- 禁止跳过构建门（Step 2）直接做功能验证。
- 禁止在未启动 Docker 全栈时执行集成测试（Step 6）。
- 禁止用 `curl` 替代 Vitest/pytest 断言——验证以测试框架断言为准，curl 仅用于探针/冒烟。
- 禁止输出"功能正常"等模糊描述——每个结论绑定到具体用例编号与其 `期望` 断言。
- 禁止将未在本技能定义的行为当作缺陷——先核对 `对应代码`，确认实际契约后再判定。
