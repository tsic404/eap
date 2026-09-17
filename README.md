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

GitHub Actions 在 PR 上运行 `lint` + `typecheck` + `test` + `build`（前端与后端两个 job）。

## 数据库迁移与部署

schema 变更通过 Alembic 管理（`apps/backend/alembic/`）。迁移脚本的部署注意事项——含 NOT VALID → VALIDATE 两阶段外键的窗口期说明——见 [`deploy/migrations.md`](deploy/migrations.md)。
