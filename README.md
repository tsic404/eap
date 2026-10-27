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
docker-compose.yml     # 本地开发环境（10 常驻 + 3 一次性引导容器）
.env.example           # 环境变量模板
.github/workflows/ci.yml  # CI 骨架
```

## 本地开发

### 依赖要求

- Node.js >= 20、pnpm 11（版本由根 `packageManager` 字段固定，建议通过 corepack 启用）
- Python >= 3.12、uv（后端依赖由 `apps/backend/uv.lock` 锁定）
- Docker / Podman
- GNU make + bash + OpenSSL（`make setup-env` 自动补全必填密钥并生成 JWT 密钥对）

### 启动全部容器

```bash
make setup-env         # 生成 .env、补全必填密钥、生成 JWT 密钥对
docker compose up -d
```

`make setup-env` 幂等：`.env` 不存在时从 `.env.example` 复制，否则原地替换仍为空、或仍是
`.env.example` 占位值（`replace-me-*`）的项——`WEAVIATE_API_KEY` / `DIFY_SECRET_KEY` /
`PLUGIN_DAEMON_KEY` / `PLUGIN_DIFY_INNER_API_KEY` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` /
`DIFY_CONSOLE_PASSWORD` / `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` 用 `openssl rand -hex 32` 生成，
`OIDC_ISSUER` 与 `DIFY_CONSOLE_EMAIL` 生成随机不可解析值；面向外部系统的生成值（IdP、Dify 控制台、
签名密钥）故意不可用，生产须按部署清单换成真实值。
`make setup-env` 另外生成 RS256 JWT 密钥对到 `deploy/keys/`（gitignored，compose 以只读 secret
挂载给 backend，本地签名用它，不读 `.env` 里的 PEM 槽位）；已填写/已生成的值不会被覆盖。
不要跳过这一步：占位值能让 compose 的 `${VAR:?}` 校验通过，但 Weaviate / Dify / plugin-daemon
的共享内部鉴权会退化成模板里公开已知的占位密钥（dev 下这些服务只在 compose 网络内可达）。
若 shell 已导出同名空变量或模板占位值（如 `export WEAVIATE_API_KEY=`，或 `set -a; . ./.env`
把占位值导出的情形），其优先级高于 `.env`，脚本会报错退出——先 `unset` 该变量再重跑。
导出的是真实值时，compose 仍以导出值为准，但脚本同样会把 `.env` 里残留的占位值换成生成值
（导出值不写入文件），保证引导后文件中不再有模板占位值。

SSO 默认走 compose 内的 `mock-idp`（与 e2e / QA 栈同一个桩 IdP）：`.env` 的 `MOCK_IDP_ISSUER` /
`MOCK_IDP_DISCOVERY_URL` / `MOCK_IDP_CLIENT_ID` 非空时（模板默认）compose 就用它们，OIDC 区块里的
prod 占位值不参与；单机 `docker compose up -d` 即可用 `alice@acme.com` 完成登录，JWT 用
`make setup-env` 生成到 `deploy/keys/` 的密钥对。浏览器入口另看 `NGINX_PORT` + `PUBLIC_ORIGIN`
（默认 80 / `http://localhost`，两处需指向同一地址；`OIDC_REDIRECT_URI` 与 CORS 允许来源由它推出）。
接真实 IdP 时把这三个 `MOCK_IDP_*` 值置空并填 `OIDC_ISSUER` / `OIDC_CLIENT_ID` /
`OIDC_CLIENT_SECRET`；后端若要用另一个网络名访问 IdP，再设 `OIDC_DISCOVERY_URL`。
`db-seed` 会幂等种入 `acme.com` 租户与 alice（agent_admin）；本地主栈与 QA 变体显式开启本地登录，
因此同一容器还会种入首个平台管理员 `admin@acme.com`（本地账号，初始口令 `Local-Admin-1`，首次登录
强制改密；可用 `LOCAL_ADMIN_EMAIL` / `LOCAL_ADMIN_PASSWORD` 覆盖）。生产形态不开启该能力，也不
自动种入本地账号。

常驻容器：`nginx`、`frontend`、`backend`、`mock-idp`、`dify-api`、`dify-worker`、
`plugin-daemon`、`postgres`、`redis`、`weaviate`。另有三个一次性引导容器，成功即退出：

- `dify-db-init`：幂等创建 `dify` 与 `dify_plugin` 两个库（后者供 plugin daemon 使用），
  每次 `up` 都跑，因此旧数据卷也能补建。
- `init_permissions`：把 plugin daemon 存储卷 chown 给 uid 1001（该镜像以非 root 运行）。
- `db-seed`：以 backend 镜像运行 `app.bootstrap`，幂等种入 mock IdP 身份映射的
  `acme.com` 租户与 `alice@acme.com`（agent_admin）；本地登录开启时还种入首个平台管理员
  （本地账号、须改密），平台已有平台管理员时不创建。在 backend healthy（schema 迁移完成）后执行。

Dify 1.x 的模型/工具插件不在 api 进程内，而由 `plugin-daemon` 托管，并使用独立的
`dify_plugin` 库；api / worker 通过 `PLUGIN_DAEMON_URL` + `PLUGIN_DAEMON_KEY` 访问它，
daemon 再以 `INNER_API_KEY_FOR_PLUGIN` 回调 api。缺任一环，知识库创建、模型列表与
流式对话都会以 "Failed to request plugin daemon" 失败。

- 前端首页：http://localhost
- 登录入口：http://localhost/login → `SSO 登录`（mock IdP 用户 `alice@acme.com`）
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

集成测试用 `testcontainers` 起真实 PostgreSQL(pgvector) 与 Redis 容器，Dify 边界保持 mock。测试数据通过 factory 函数构造（每 worker 独立 tenant 前缀），每个用例跑在事务回滚内。受 cgroup 限制的本地 Docker 启动 Ryuk 会报 `operation not permitted` 并导致全部用例超时，集成测试的 conftest 已默认 `TESTCONTAINERS_RYUK_DISABLED=true` 关闭 Ryuk（正常退出时 testcontainers 的 context manager 负责停容器；测试被强杀时残留容器用 `docker container prune -f` 清理）。容器启动/拉取同样经由宿主 Docker daemon：conftest 把 docker-py 客户端的读超时从默认 60s 放宽到 300s，避免高负载（例如 compose 冷启动、并发构建）下夹具以 `ReadTimeout` 集体失败。

### 验收用例（技术验收 TC-tech-*）

设计仓 `docs/03-acceptance.md` 的每条技术用例在实现仓有唯一可执行形态：`apps/backend/tests/acceptance/` 下的模块名对应用例标识（`TC-tech-kb-01` → `test_tc_tech_kb_01.py`），模块内断言对应该用例的可观察判据；执行入口按标识运行：

```bash
cd apps/backend
uv run python -m tests.acceptance                  # 全部后端 TC-tech-* 用例
uv run python -m tests.acceptance TC-tech-kb-01    # 单条按标识执行
uv run python -m tests.acceptance --list           # 列出可用标识
```

验收用例与集成测试共用同一套夹具（Testcontainers PostgreSQL/Redis + Dify mock），同样需要 Docker；CI 的 `pytest -m "not perf"` 会连带执行整套用例（也可 `uv run pytest tests/acceptance`）。

前端用例（`TC-tech-ui-01`、`TC-kb-06`）位于 `apps/frontend/tests/acceptance/`，模块名与标识一一对应，执行入口等价：

```bash
cd apps/frontend
pnpm acceptance                  # 全部前端用例
pnpm acceptance TC-kb-06         # 单条按标识执行
pnpm acceptance --list           # 列出可用标识
```

根目录另有 `make acceptance-ui`（单条：`make acceptance-ui TC=TC-kb-06`）。`TC-tech-ops-02` 末条「浏览器请求实际落在同源相对路径」由 `apps/frontend/e2e/same-origin.spec.ts` 在真实浏览器中取证，随 `pnpm test:e2e` 执行。

## QA 环境（本地族完整形态）

一键拉起与 Playwright e2e 回归同源的本地族栈：入口反向代理 + mock IdP + backend + eap_e2e DB + frontend，以及完整的推理引擎（Dify api / worker / 插件宿主 / Weaviate），无需再手动 `docker build` / `uv sync` / 建库 / socat。容器集合与本地主栈一致（C-88：10 个常驻 + 3 个一次性引导），入口用反向代理代替 nginx。

```bash
make qa-up     # 生成 JWT 密钥与栈密钥 + 构建并启动全部服务
make qa-down   # 停止并移除全部服务（保留数据卷）
```

- 入口：http://localhost:8090（反向代理把前端与后端统一到同一 origin）
- 登录：首页 → `SSO 登录`；mock IdP 用户 `alice@acme.com` 落到 seed 的 `acme.com` 租户
- 后端日志：`docker compose --env-file deploy/qa/qa.env -f docker-compose.qa.yml logs -f backend`
- 就绪：`curl http://localhost:8090/api/health/ready` 期望 200，且 `checks` 三项均 `ok`

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| reverse-proxy | 8090 | 反向代理（`/api/*` → backend，其余 → frontend；`/healthz` 自检） |
| mock-idp | 4000 | mock OIDC IdP（浏览器直达其 authorize 端点）；同时提供内网确定性模型端点 `:4100` |
| frontend | 内部 3000 | Next.js 生产构建 |
| backend | 内部 3001 | FastAPI，含 JWT 密钥生成 |
| dify-api / dify-worker | 内部 5001 | 推理引擎 API 与异步 worker |
| plugin-daemon | 内部 5002 | 模型 / 工具插件宿主（`dify_plugin` 库） |
| weaviate | 内部 8080 | 向量库（与主栈同版本） |
| postgres | 5433 | `eap_e2e` 库；另由 `dify-db-init` 幂等创建 `dify` / `dify_plugin` |
| redis | 6380 | 会话 / 限流 / 引擎队列 |

说明：

- `make qa-up` 幂等：JWT 密钥（`deploy/qa/keys/`）与栈密钥（`deploy/qa/qa.env`）首次生成后复用，均已在 gitignore 内；仓库不落密钥。移除了 `deploy/qa/qa.env` 时须连同数据卷一起清理（`down -v`）再重建，否则引擎控制台账号与生成值不再匹配。
- `db-seed` 在种入租户 / 账号后调用 `deploy/qa/dify_bootstrap.py`：创建引擎控制台账号、安装固定版本的 OpenAI 兼容模型插件，并把 `mock-idp` 容器内确定性模型注册为工作区模型。它的一次性退出码属于就绪基线（C-88），引擎配置不完整时不会伪装成就绪。工作区模型为 `langgenius/openai_api_compatible/openai_api_compatible` / `mock-gpt`，创建智能体时直接引用。
- 容器化后浏览器与后端分处不同网络视角：后端经 `OIDC_DISCOVERY_URL` 走 compose 网络访问 IdP 的 token/userinfo/JWKS，同时按浏览器侧 `OIDC_ISSUER` 校验 id_token（对应 `mock-idp.mjs` 的 `MOCK_IDP_ISSUER` / `MOCK_IDP_BACKEND_BASE`）。
- 连数据卷一起彻底清理：`docker compose --env-file deploy/qa/qa.env -f docker-compose.qa.yml down -v`。

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

### 容器内 e2e 运行器（宿主只需 Docker）

宿主没有 pnpm、没有可执行的 Playwright 浏览器、也没有后端 venv 时，用容器把浏览器用例跑在
QA 栈上：

```bash
make e2e                                              # 默认 e2e/journeys/tc-auth-08-local-login.spec.ts
make e2e E2E_SPEC=e2e/journeys/tc-auth-09-....spec.ts  # 换一条自足 journey
```

**覆盖范围**：runner 只接**自足 spec**——只依赖 QA 栈的租户 / 本地账号种子与普通 Playwright
的 journey。完整套件仍走 `playwright.config.mjs`（自起 4 个 webServer 并重放 DB / Redis 准备）；
依赖该 global setup 的用例（如 `sso.spec.ts`、`admin-rbac.spec.ts`）或依赖应用专属包的用例
（`a11y.spec.ts` 需要 `@axe-core/playwright`）不在本入口范围内。

`scripts/e2e.sh` 一条命令完成：

1. `scripts/gen-jwt-keys.sh deploy/qa/keys` 与 `scripts/gen-qa-secrets.sh` 生成（或复用）QA 的
   JWT 密钥与栈密钥；
2. `docker compose --env-file deploy/qa/qa.env -f docker-compose.qa.yml up -d --build` 起 QA 栈，
   等到 C-88 的 10 个常驻服务（含 dify-api / dify-worker / plugin-daemon / weaviate /
   reverse-proxy）都是 `healthy`、一次性 bootstrap（含引擎控制台 / 模型插件配置）退出码 0、
   入口 `/api/health` 返回 200，才放行用例；
3. 清掉上一轮遗留的登录状态（只删 Redis 的 `login:*` 与 `ratelimit:login:*`，会话等其它键不动）——
   QA 栈的 Redis 是长期存活的，不清理会让上一轮的锁定 / 限流桶漏进本轮；
4. 在 backend 容器里用应用自己的口令模块种入本地账号（`deploy/e2e/seed_accounts.py`，幂等，
   对应 `e2e/helpers/accounts.cjs`）；
5. 从 `pnpm-lock.yaml` 取 `@playwright/test` 版本作为 build arg，用 `deploy/e2e/Dockerfile`
   （`node:22-bookworm-slim` + `npm ci` 按 `deploy/e2e/package-lock.json` + `playwright install
   --with-deps chromium`）构建 runner 镜像；版本与 `deploy/e2e/package.json` 不一致时构建失败；
6. 在 runner 容器里用 `playwright.stack.config.mjs` 对**已起的栈**跑指定 spec；该配置不启动
   webServer（`playwright.config.mjs` 的自起栈入口保持原样）。

产物：`artifacts/playwright/report/index.html`（HTML 报告）与
`artifacts/playwright/test-results/`（截图 / 轨迹）。

已知前提与环境变量：

- 宿主需 Linux + Docker（runner 用 `--network host` 让浏览器从 `http://localhost:8090` 同源
  访问，与 CI 一致）；其它平台用 `E2E_DOCKER_NETWORK` 指定可解析入口的网络。
- `E2E_BASE_URL` 覆盖入口地址（默认 `http://localhost:${QA_PROXY_PORT:-8090}`），不写死。
- `E2E_SPEC` 选择自足用例，`E2E_ARTIFACTS` 改产物目录，`E2E_STACK_TIMEOUT` 调整栈启动等待上限。
- `E2E_LOGIN_RATE_LIMIT`（默认 `300`）只在本轮运行期间抬高登录节流（整套用例由同一地址驱动，
  锁定用例本身就要 6 次登录请求）；**起栈成功之后**的任何退出（含失败 / 中断）都会把 backend
  重建回启动前的值，默认即契约值的 10 次/分，并在退出前校验。起栈阶段失败时仅当此前已有
  backend 才恢复，冷启动失败不另建孤立 backend。
- 脚本把临时目录（`TMPDIR`）放在 `E2E_ARTIFACTS/.tmp` 下，避免小 `/tmp` 或配额受限的宿主在
  镜像构建成功后写不出 compose 元数据；`E2E_TMPDIR` 可整体覆盖。

## 数据库迁移与部署

schema 变更通过 Alembic 管理（`apps/backend/alembic/`）。迁移脚本的部署注意事项——含 NOT VALID → VALIDATE 两阶段外键的窗口期说明——见 [`deploy/migrations.md`](deploy/migrations.md)。

### 生产部署

```bash
cp .env.example .env          # 占位值与开发默认值都按 deploy/checklist.md 逐项替换
make setup-env                # 清除全部 replace-me-* 占位值：共享密钥与 PEM 槽位换成随机值、
                              # OIDC 与 Dify 控制台值换成随机不可解析值（均非生产可用值）
# 生产仍须手工填写 OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET、OIDC_REDIRECT_URI（真实公网
# https 回调，COOKIE_SECURE=true 下 http 回调会被浏览器丢弃 cookie）、JWT_PRIVATE_KEY /
# JWT_PUBLIC_KEY（生产自有密钥对，生成方式见 deploy/checklist.md）、DIFY_CONSOLE_EMAIL /
# DIFY_CONSOLE_PASSWORD（真实控制台账号），并把 POSTGRES_PASSWORD 连同内嵌同一口令的
# DATABASE_URL 一起换掉
docker compose -f docker-compose.prod.yml config -q   # 校验必填项，期望退出码 0
docker compose -f docker-compose.prod.yml up -d --build
curl http://localhost/api/health/ready   # 期望 200（dify 项还要求控制台账号可登录且已配置模型供应商）
```

生产 compose（`docker-compose.prod.yml`）以非 root 用户运行后端镜像，所有密钥通过 `${VAR:?...}` 强制注入、无硬编码回退。`.env.example` 的占位值只保证模板自身可校验（`cp` 后 `config -q` 即通过），**不代表可以直接上线**：未替换时 SSO 登录、令牌签发与 Dify 管理功能会失败，模板里的 `eap_password` 弱口令与 `http://localhost` 回调也会被原样带进生产。完整步骤见 [`deploy/checklist.md`](deploy/checklist.md)。
