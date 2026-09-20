# EAP — 企业智能体平台 (Enterprise Agent Platform)

企业智能体平台：员工智能体资产管理、知识库 RAG、工具调用、会话闭环。

- 前端：Next.js 15（`apps/frontend`）
- 后端：Python / FastAPI（`apps/backend`）
- 共享契约：`packages/api-contract`

## Monorepo 结构

```
apps/
  frontend/            # Next.js App Router
  backend/             # FastAPI
packages/
  api-contract/        # 前后端共享 API 契约（TypeScript）
deploy/
  nginx/               # 反向代理配置
docker-compose.yml     # 8 容器本地开发环境
.env.example           # 环境变量模板
.github/workflows/ci.yml  # CI 骨架
```

## 本地开发

### 依赖要求

- Node.js >= 20、pnpm 11（版本由根 `packageManager` 字段固定，建议通过 corepack 启用）
- Python >= 3.12、uv（后端依赖由 `apps/backend/uv.lock` 锁定）
- Docker / Podman

### 启动全部 8 个容器

```bash
cp .env.example .env   # 按需修改
docker compose up -d
```

容器：`nginx`、`frontend`、`backend`、`dify-api`、`dify-worker`、`postgres`、`redis`、`weaviate`。

- 前端首页：http://localhost
- 健康检查：http://localhost/api/health/live

### 单独运行前端 / 后端

```bash
# 前端
pnpm install
pnpm --filter @eap/frontend dev

# 后端
cd apps/backend
uv sync --extra dev --locked
uv run uvicorn app.main:app --reload --port 3001
```

## CI

GitHub Actions 在 PR 上运行 `lint` + `typecheck` + `test` + `build`（前端与后端两个 job），并在 main 分支 push 时构建并推送 Docker 镜像到 GHCR。性能门禁（Agent 列表 P95 < 240ms）在 `pytest -m perf` 独立步骤运行，≥20% 退化会拦截合并。

## 测试

```bash
# 单元 + API 测试（需要本地 PostgreSQL，TEST_DATABASE_URL 指向它）
cd apps/backend
uv sync --extra dev --locked
TEST_DATABASE_URL=postgresql+asyncpg://eap:eap_password@localhost:5432/eap uv run pytest

# 集成测试（httpx.AsyncClient + Testcontainers PostgreSQL/Redis，需 Docker）
uv run pytest tests/integration

# 性能门禁（Agent 列表 P95 < 240ms）
uv run pytest -m perf
```

集成测试用 `testcontainers` 起真实 PostgreSQL(pgvector) 与 Redis 容器，Dify 边界保持 mock。测试数据通过 factory 函数构造（每 worker 独立 tenant 前缀），每个用例跑在事务回滚内。

### SSO e2e 回归（Playwright）

```bash
pnpm install
npx playwright install chrome   # 首次需下载浏览器
cd apps/backend && uv sync --extra dev --locked && cd ../..
pnpm --filter @eap/frontend test:e2e
```

`e2e/sso.spec.ts` 用真实浏览器走完整 OIDC 授权码 + PKCE 流程，全链路本地化、不依赖真实企业 IdP：

- **IdP**：`e2e/mock-idp.mjs` 进程内提供 discovery / JWKS / authorize / token / userinfo，RS256 密钥每次运行生成。
- **Redis**：`e2e/global-setup.mjs` 自动起一次性 `redis:7-alpine` 容器（`127.0.0.1:6380`，容器名带进程 PID，持久化关闭故不会进入 MISCONF），并把容器 ID 写入所有权标记；`e2e/global-teardown.mjs` 只删除本次运行创建的容器。未设 `E2E_REDIS_URL` 时不再复用宿主 Redis；端口被占（如崩溃遗留的 `eap-e2e-redis-*` 容器）则报错并提示清理。
- **数据库**：`global-setup.mjs` 重置一次性库 `eap_e2e`（库名必须带 `_e2e`/`_test` 后缀，否则需 `E2E_DESTRUCTIVE=1`）并 seed `acme.com` tenant。

前置：Docker（起 Redis 容器）、本地 PostgreSQL（`localhost:5432`，`eap`/`eap_password`）、后端 venv（或 `E2E_BACKEND_PYTHON` 指定解释器）。

环境变量覆盖：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `E2E_REDIS_URL` | 空 | 自备 Redis 时指定（如 `redis://localhost:6379/0`）；设置后 harness 跳过自管理容器，teardown 也不会触碰它 |
| `E2E_REDIS_PORT` | `6380` | 自管理 Redis 容器的宿主端口 |
| `E2E_PSQL_DSN` / `E2E_DATABASE_URL` | `…/eap_e2e` | 一次性库 DSN |

## 数据库迁移与部署

schema 变更通过 Alembic 管理（`apps/backend/alembic/`）。迁移脚本的部署注意事项——含 NOT VALID → VALIDATE 两阶段外键的窗口期说明——见 [`deploy/migrations.md`](deploy/migrations.md)。

### 生产部署

```bash
cp .env.example .env          # 填全所有必需变量（含 Dify/JWT/Weaviate 密钥）
docker compose -f docker-compose.prod.yml up -d --build
curl http://localhost/api/health/ready   # 期望 200
```

生产 compose（`docker-compose.prod.yml`）以非 root 用户运行后端镜像，所有密钥通过 `${VAR:?...}` 强制注入、无硬编码回退。完整步骤见 [`deploy/checklist.md`](deploy/checklist.md)。
