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
docker-compose.yml     # 本地开发环境（9 常驻 + 2 一次性引导容器）
.env.example           # 环境变量模板
.github/workflows/ci.yml  # CI 骨架
```

## 本地开发

### 依赖要求

- Node.js >= 20、pnpm 11（版本由根 `packageManager` 字段固定，建议通过 corepack 启用）
- Python >= 3.12、uv（后端依赖由 `apps/backend/uv.lock` 锁定）
- Docker / Podman
- GNU make + bash + OpenSSL（`make setup-env` 自动补全必填密钥）

### 启动全部容器

```bash
make setup-env         # 从 .env.example 生成 .env，并补全必填密钥
docker compose up -d
```

`make setup-env` 幂等：`.env` 不存在时从 `.env.example` 复制，否则原地补全仍为空的
`WEAVIATE_API_KEY` / `DIFY_SECRET_KEY` / `PLUGIN_DAEMON_KEY` / `PLUGIN_DIFY_INNER_API_KEY`
（`openssl rand -hex 32`），已填写的值不会被覆盖。
若 shell 已导出同名空变量（如 `export WEAVIATE_API_KEY=`），其优先级高于 `.env` 会让 compose
仍硬失败，脚本会报错退出——先 `unset WEAVIATE_API_KEY DIFY_SECRET_KEY PLUGIN_DAEMON_KEY PLUGIN_DIFY_INNER_API_KEY` 再重跑。

常驻容器：`nginx`、`frontend`、`backend`、`dify-api`、`dify-worker`、`plugin-daemon`、
`postgres`、`redis`、`weaviate`。另有两个一次性引导容器，成功即退出：

- `dify-db-init`：幂等创建 `dify` 与 `dify_plugin` 两个库（后者供 plugin daemon 使用），
  每次 `up` 都跑，因此旧数据卷也能补建。
- `init_permissions`：把 plugin daemon 存储卷 chown 给 uid 1001（该镜像以非 root 运行）。

Dify 1.x 的模型/工具插件不在 api 进程内，而由 `plugin-daemon` 托管，并使用独立的
`dify_plugin` 库；api / worker 通过 `PLUGIN_DAEMON_URL` + `PLUGIN_DAEMON_KEY` 访问它，
daemon 再以 `INNER_API_KEY_FOR_PLUGIN` 回调 api。缺任一环，知识库创建、模型列表与
流式对话都会以 "Failed to request plugin daemon" 失败。

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

集成测试用 `testcontainers` 起真实 PostgreSQL(pgvector) 与 Redis 容器，Dify 边界保持 mock。测试数据通过 factory 函数构造（每 worker 独立 tenant 前缀），每个用例跑在事务回滚内。受 cgroup 限制的本地 Docker 启动 Ryuk 会报 `operation not permitted` 并导致全部用例超时，此时用 `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration` 关闭 Ryuk 运行；关闭后若测试被中断，残留容器用 `docker container prune -f` 清理。

## QA 环境（SSO 端到端）

一键拉起与 Playwright e2e 回归同源的 SSO 栈（mock IdP + backend + eap_e2e DB + frontend + 反向代理），无需再手动 `docker build` / `uv sync` / 建库 / socat。

```bash
make qa-up     # 生成 JWT 密钥 + 构建并启动全部服务
make qa-down   # 停止并移除全部服务（保留 eap_e2e 数据卷）
```

- 入口：http://localhost:8090（反向代理把前端与后端统一到同一 origin）
- 登录：首页 → `SSO 登录`；mock IdP 用户 `alice@acme.com` 落到 seed 的 `acme.com` 租户
- 后端日志：`docker compose -f docker-compose.qa.yml logs -f backend`

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| reverse-proxy | 8090 | 反向代理（`/api/*` → backend，其余 → frontend） |
| mock-idp | 4000 | mock OIDC IdP（浏览器直达其 authorize 端点） |
| frontend | 内部 3000 | Next.js 生产构建 |
| backend | 内部 3001 | FastAPI，含 JWT 密钥生成 |
| postgres | 5433 | `eap_e2e` 库（首次启动 seed `acme.com` 租户） |
| redis | 6380 | 会话 / 限流 |

说明：

- `make qa-up` 幂等：JWT 密钥首次生成后复用（`deploy/qa/keys/`，已 gitignore），租户 seed 为幂等 SQL。
- 容器化后浏览器与后端分处不同网络视角：后端经 `OIDC_DISCOVERY_URL` 走 compose 网络访问 IdP 的 token/userinfo/JWKS，同时按浏览器侧 `OIDC_ISSUER` 校验 id_token（对应 `mock-idp.mjs` 的 `MOCK_IDP_ISSUER` / `MOCK_IDP_BACKEND_BASE`）。
- 连数据库卷一起彻底清理：`docker compose -f docker-compose.qa.yml down -v`。

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
make setup-env                # 生成 .env 并补全 WEAVIATE_API_KEY / DIFY_SECRET_KEY
                              # 其余必需变量（DB/Redis/Dify 凭据/OIDC/JWT）仍需手工填写
docker compose -f docker-compose.prod.yml up -d --build
curl http://localhost/api/health/ready   # 期望 200
```

生产 compose（`docker-compose.prod.yml`）以非 root 用户运行后端镜像，所有密钥通过 `${VAR:?...}` 强制注入、无硬编码回退。完整步骤见 [`deploy/checklist.md`](deploy/checklist.md)。
