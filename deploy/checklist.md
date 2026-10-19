# 部署检查清单（架构文档 附录 C）

按顺序执行；任一步失败即停止并修复后再继续。所有 `?` 标记的变量来自 `.env`。

## 部署前检查

- [ ] `.env` 已按本清单逐项替换环境相关值（`replace-me-*` 只是让模板通过 compose 的 `${VAR:?}` 校验，不能用于生产）：
  - compose 强制占位项：`OIDC_ISSUER`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET`、`JWT_PRIVATE_KEY`、`JWT_PUBLIC_KEY`、`DIFY_CONSOLE_EMAIL`、`DIFY_CONSOLE_PASSWORD`、`DIFY_SECRET_KEY`、`WEAVIATE_API_KEY`、`PLUGIN_DAEMON_KEY`、`PLUGIN_DIFY_INNER_API_KEY`
  - dev 默认可用的值，生产必须替换：`POSTGRES_PASSWORD`（模板 `eap_password`；改口令要同步改内嵌同一口令的 `DATABASE_URL`）、`OIDC_REDIRECT_URI`（模板留空走 compose 默认，生产填真实公网 https 回调地址——`COOKIE_SECURE=true` 下 http 回调会让浏览器丢弃刷新令牌 cookie）
  - 其余必需项按部署拓扑核对，模板值即 compose 内网地址，通常无需改动：`DATABASE_URL`、`REDIS_URL`、`DIFY_API_BASE_URL`
  - `make setup-env` 会替换模板里的全部 `replace-me-*` 占位值：共享密钥 / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` / `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` / `DIFY_CONSOLE_PASSWORD` 换成随机值，`OIDC_ISSUER` / `DIFY_CONSOLE_EMAIL` 换成随机不可解析值——这些生成值只保证占位值不再存活，均非生产可用值，生产必须按本清单逐项换成真实值
  - `OIDC_*` 取自 IdP 的 OIDC 应用注册；`DIFY_CONSOLE_EMAIL` / `DIFY_CONSOLE_PASSWORD` 是 Dify 控制台管理员账号
  - `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` 是 RS256 密钥对：生成后把 PEM 以双引号内联进 `.env`（多行或 `\n` 转义均可，prod compose 会原样传给容器）；`JWT_PRIVATE_KEY_FILE` / `JWT_PUBLIC_KEY_FILE` 只适用于挂载了密钥文件的部署（如 QA 栈）：
    `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt_private.pem`
    `openssl pkey -in jwt_private.pem -pubout -out jwt_public.pem`
- [ ] `LOCAL_LOGIN_ENABLED` 保持模板默认关闭，除非部署方显式决定开启本地账号口令登录。生产形态不自动种入本地账号，平台也不提供生产侧「首个本地平台管理员」的引导路径：第 6 步 `app.seed` 种入的初始平台管理员是 SSO 来源、无本地口令，其 `default` 租户没有租户域；成员管理只能由已登录的平台管理员调用，建出的账号固定为 `employee`（租户域为空时建号 422），平台没有角色提升入口。启用本地登录的部署须由部署方在部署阶段自行创建租户域与首个 `platform_admin` 本地账号（租户域内邮箱、口令自定、须改密），在该引导就位前保持关闭。
- [ ] `docker compose -f docker-compose.prod.yml config -q` 退出码 0（所有 `${VAR:?}` 必填项已解析）
- [ ] PostgreSQL 已运行并创建数据库（`CREATE DATABASE eap`，另由 `dify-db-init` 幂等创建 `dify` / `dify_plugin`）
- [ ] Redis 已运行并可连接（`redis-cli ping` → `PONG`）
- [ ] Dify API 已部署并可通过内网访问（`curl http://dify-api:5001/v1/info`）
- [ ] Dify Worker 已部署（异步索引任务必需）
- [ ] Dify Plugin Daemon 已部署并可访问（`curl http://plugin-daemon:5002/health/check`；知识库与对话依赖它托管模型插件）
- [ ] Dify 已配置模型供应商（控制台安装模型插件并设置默认 embedding / LLM 模型；否则知识库创建返回 `Default model not found for text-embedding`，对话无法生成，且 `/api/health/ready` 的 `dify` 项报 `error`）
- [ ] Weaviate 已部署（向量检索必需）

## 部署步骤

1. **数据库迁移**：`cd apps/backend && alembic upgrade head`
2. **构建前端**：`cd apps/frontend && pnpm build`
3. **构建后端镜像**：`cd apps/backend && docker build -t eap-backend .`
4. **启动服务**：`docker compose -f docker-compose.prod.yml up -d`
5. **验证健康**：`curl http://localhost/api/health/ready` → 200，且 `checks` 中 `database`/`redis`/`dify` 均为 `ok`（`dify` 为 `ok` 要求 `DIFY_CONSOLE_EMAIL` / `DIFY_CONSOLE_PASSWORD` 可登录控制台，且工作区已有配置凭据的模型供应商）
6. **创建初始租户与首位平台管理员**：`cd apps/backend && python -m app.seed`（该管理员为 SSO 来源、无本地口令；本版本平台不提供「首个本地平台管理员」引导，若本部署开启本地登录，须由部署方按「部署前检查」自行创建后再开启）
7. **验证登录**：浏览器访问 `http://localhost/login` → SSO 登录 → 成功跳转 `/user`

## 部署后验证

- [ ] SSO 登录成功
- [ ] 创建智能体（表单提交 → 列表可见）
- [ ] 创建知识库 + 上传文档 → 等待索引完成 → 召回测试返回结果
- [ ] 员工端发起对话 → SSE 流式输出 → 引用来源展示
- [ ] 触发高风险工具 → 任务中心出现待审批任务 → 审批后工具执行
- [ ] 管理端可查看运行日志和 Trace 详情
- [ ] `GET /metrics` Prometheus 指标端点可访问
- [ ] `GET /docs` FastAPI OpenAPI 文档可访问
