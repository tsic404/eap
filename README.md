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

集成测试用 `testcontainers` 起真实 PostgreSQL(pgvector) 与 Redis 容器，Dify 边界保持 mock。测试数据通过 factory 函数构造（每 worker 独立 tenant 前缀），每个用例跑在事务回滚内。受 cgroup 限制的本地 Docker 启动 Ryuk 会报 `operation not permitted` 并导致全部用例超时，此时用 `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration` 关闭 Ryuk 运行；关闭后若测试被中断，残留容器用 `docker container prune -f` 清理。

## 数据库迁移与部署

schema 变更通过 Alembic 管理（`apps/backend/alembic/`）。迁移脚本的部署注意事项——含 NOT VALID → VALIDATE 两阶段外键的窗口期说明——见 [`deploy/migrations.md`](deploy/migrations.md)。

### 生产部署

```bash
cp .env.example .env          # 填全所有必需变量（含 Dify/JWT/Weaviate 密钥）
docker compose -f docker-compose.prod.yml up -d --build
curl http://localhost/api/health/ready   # 期望 200
```

生产 compose（`docker-compose.prod.yml`）以非 root 用户运行后端镜像，所有密钥通过 `${VAR:?...}` 强制注入、无硬编码回退。完整步骤见 [`deploy/checklist.md`](deploy/checklist.md)。
