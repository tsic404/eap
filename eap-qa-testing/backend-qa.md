# 后端模块 QA 测试用例（backend-qa.md）

> 覆盖 `apps/backend/` 当前已实现的后端模块，用例编号沿用 SC01–SC08（延续 TSI-2848），
> 子项用 SC0X-N 格式。每条用例可执行、可判定：`前置条件` 说明依赖状态，`步骤` 是具体操作，
> `期望` 是可断言的预期结果，`对应代码` 指向真实文件、函数或已存在的测试。

## 运行前提

- 进入 `apps/backend/`，执行 `pip install -e ".[dev]"`。
- 纯契约/单元用例（SC02、SC04、SC05、SC06、SC08，及 SC03 中直接调用中间件方法的用例）不依赖外部服务，可直接跑。
- 走完整 HTTP 管道的用例用 FastAPI `TestClient`（`tests/conftest.py::client` fixture）。
- 涉及真实依赖的用例（SC01 ready 探针、SC07 Seed 入库）需启动 postgres / redis / dify，或走 testcontainers（§31.1.3 集成层）。

---

## SC01: 健康检查（`app/health.py`）

三个探针（`/api/health`、`/api/health/live`、`/api/health/ready`）均为 raw 响应——不含统一
`{"data": …}` 包裹，这是共享 API 契约对健康探针的显式例外，供负载均衡与编排系统直接消费。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC01-1 | `GET /api/health` 返回服务信息 | 应用已启动 | 请求 `GET /api/health` | 200；body == `{"service":"eap-backend","version":"0.1.0","status":"ok"}`（version 为 `settings.app_version`），无 `data` 外层 | `app/health.py::health` · `tests/test_health.py::test_health_info_returns_raw_service_info` |
| SC01-2 | `GET /api/health/live` 返回存活探针 | 应用已启动 | 请求 `GET /api/health/live` | 200；body == `{"status":"ok"}`，无 `data` 外层 | `app/health.py::health_live` · `tests/test_health.py::test_health_live_returns_raw_ok` |
| SC01-3 | `GET /api/health/ready` 报告依赖状态 | 应用已启动；DB/Redis/Dify 按场景可用或不可用 | 请求 `GET /api/health/ready` | 全可用 → 200 + `{"status":"ok","checks":{"database":"ok","redis":"ok","dify":"ok"}}`；任一不可用 → 503 + `status=="degraded"` 且对应 `check=="error"`；`checks` 键集合恒为 `{database,redis,dify}` | `app/health.py::health_ready` · `_ping_database` / `_ping_redis` / `_ping_dify`（单探针超时 2.0s） |
| SC01-4 | Health 端点不被 TransformMiddleware 包装 | 应用已启动 | 请求任一 `/api/health*` 端点并检查响应体 | 响应体无 `{"data": …}` 外层；`TransformMiddleware._is_health_path(path)` 对 `/api/health` 及其子路径返回 True | `app/middleware/transform.py::_is_health_path` |

---

## SC02: 配置管理（`app/config.py`）

`Settings` 基于 pydantic-settings，`case_sensitive=False`、`extra="ignore"`，环境变量（如 `DATABASE_URL`）直接映射到同名字段。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC02-1 | 默认值正确 | 无相关环境变量注入 | 实例化 `Settings(_env_file=None)` 并断言 | `app_env=="development"`、`app_version=="0.1.0"`、`database_url` 以 `postgresql+asyncpg://` 开头、`redis_url` 以 `redis://` 开头、`log_json is True` | `app/config.py::Settings` · `tests/test_config.py::test_settings_defaults` |
| SC02-2 | 环境变量覆盖 | 注入 `DATABASE_URL` / `REDIS_URL` / `OIDC_ISSUER` / `RATE_LIMIT_REQUESTS` | 实例化 `Settings(_env_file=None)` 并断言 | 各字段等于注入值，如 `rate_limit_requests == 42`（字符串自动转 int） | `app/config.py::Settings` · `tests/test_config.py::test_settings_read_environment_variables` |
| SC02-3 | CORS_ALLOWED_ORIGINS JSON 数组解析 | 注入 `CORS_ALLOWED_ORIGINS='["http://a.example","http://b.example"]'` | 实例化并断言 | `cors_allowed_origins == ["http://a.example","http://b.example"]`（JSON 字符串解析为 list） | `app/config.py::Settings` · `tests/test_config.py::test_settings_parse_cors_list` |
| SC02-4 | `get_settings()` 缓存生效 | — | 连续两次调用 `get_settings()` 并比较对象身份 | `get_settings() is get_settings()` 为 True（`@lru_cache` 单例） | `app/config.py::get_settings` |

---

## SC03: 中间件管道（`app/middleware/`）

管道执行顺序（最外层 → 最内层，见 `app/main.py::create_app`）：
`RequestContext → Helmet → CORS → RateLimit → JWT → Tenant → Roles → Transform → router`。
`RequestContext` 包裹一切用于访问日志；`Transform` 最内层，只包 handler 响应。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC03-1 | Helmet 安全头存在 | 应用已启动 | 请求任意非流式端点并检查响应头 | 存在 `x-content-type-options: nosniff`、`x-frame-options: DENY`、`referrer-policy: strict-origin-when-cross-origin`、`x-xss-protection: 0`、`permissions-policy: camera=(), microphone=(), geolocation=()` | `app/middleware/security.py::HelmetMiddleware._HEADERS` · `tests/test_middleware.py::test_helmet_headers_present` |
| SC03-2 | CORS 白名单 origin 放行 | 应用已启动 | 带 `Origin: http://localhost:3000` 请求任意端点 | 200（业务结果）；响应头含 `access-control-allow-origin: http://localhost:3000` 与 `vary: Origin` | `app/middleware/security.py::CORSMiddleware` · `tests/test_middleware.py::test_cors_allows_whitelisted_origin` |
| SC03-3 | CORS 非白名单 origin 拒绝 | 应用已启动 | 带 `Origin: http://evil.example` 请求 | 403；body == `{"error":{"code":"FORBIDDEN","message":"Origin not allowed by CORS policy"}}` | `app/middleware/security.py::CORSMiddleware` · `tests/test_middleware.py::test_cors_rejects_non_whitelisted_origin` |
| SC03-4 | CORS preflight（OPTIONS） | 应用已启动；请求带 `Origin` + `Access-Control-Request-Method` | 对端点发 `OPTIONS` | 204；响应头含 `access-control-allow-methods: GET, POST, PUT, PATCH, DELETE, OPTIONS`、`access-control-max-age: 600`、`access-control-allow-origin` | `app/middleware/security.py::CORSMiddleware._preflight` · `tests/test_middleware.py::test_cors_preflight_whitelisted_origin` |
| SC03-5 | 请求 ID 自动生成并回写 | 应用已启动；入站请求无 `X-Request-Id` | 请求任意端点并检查响应头 | 响应头含 `X-Request-Id`，值为新生成的 UUID（`str(uuid.uuid4())`） | `app/middleware/request_context.py::RequestContextMiddleware` · `tests/test_middleware.py::test_request_id_generated_and_echoed` |
| SC03-6 | 请求 ID 从入站头透传 | 应用已启动；入站带 `X-Request-Id: trace-me` | 请求并检查响应头 | 响应头 `X-Request-Id == "trace-me"`（大小写不敏感匹配入站头） | `app/middleware/request_context.py::_extract_request_id` · `tests/test_middleware.py::test_request_id_generated_and_echoed` |
| SC03-7 | 速率限制超限返回 429 | `rate_limit_enabled=True`；窗口内请求数达 `rate_limit_requests` 上限；目标非 `/api/health*` | 发第 N+1 个请求 | 429；body == `{"error":{"code":"RATE_LIMITED","message":"Rate limit exceeded"}}` | `app/middleware/security.py::RateLimitMiddleware` · `tests/test_middleware.py::test_rate_limit_rejects_excess_requests` |
| SC03-8 | 速率限制忽略伪造 X-Forwarded-For | `trusted_proxy_count=0`；请求带 `X-Forwarded-For: 1.2.3.4, 5.6.7.8`，socket peer 为 `9.9.9.9` | 调用 `RateLimitMiddleware._client_ip(scope)` | 返回 `"9.9.9.9"`（socket peer，忽略 XFF） | `app/middleware/security.py::RateLimitMiddleware._client_ip` · `tests/test_middleware.py::test_rate_limiter_ignores_spoofed_x_forwarded_for` |
| SC03-9 | trusted_proxy_count=1 提取 rightmost hop | `trusted_proxy_count=1`；`X-Forwarded-For: 1.2.3.4, 5.6.7.8` | 调用 `_client_ip(scope)` | 返回 `"5.6.7.8"`（`hops[len(hops)-trusted_proxy_count]`，最右侧 hop，忽略左侧可伪造值） | `app/middleware/security.py::RateLimitMiddleware._client_ip` · `tests/test_middleware.py::test_rate_limiter_ignores_spoofed_x_forwarded_for` |
| SC03-10 | 退化 X-Forwarded-For 回退不抛异常 | `trusted_proxy_count=1`；`X-Forwarded-For: ", ,"`（仅分隔符），socket peer `9.9.9.9` | 调用 `_client_ip(scope)` | 返回 `"9.9.9.9"`，不抛 `IndexError` | `app/middleware/security.py::RateLimitMiddleware._client_ip` · `tests/test_middleware.py::test_rate_limiter_handles_degenerate_x_forwarded_for` |
| SC03-11 | JWT 无公钥时不信任 | `jwt_public_key=""`；请求带任意 Bearer token | 请求返回 `request.state` 的鉴权端点 | `user_id is None`、`tenant_id is None`、`role == ""`（identity 不注入） | `app/middleware/security.py::JWTMiddleware._decode` · `tests/test_middleware.py::test_jwt_without_public_key_is_not_trusted` |
| SC03-12 | JWT 有公钥时验签并注入 identity | 配置有效 RS256 `jwt_public_key`；token 由匹配私钥签发，payload 含 `sub`/`tenantId`/`role` | 带 Bearer token 请求 | 200；`request.state` 注入 `user_id==sub`、`tenant_id==tenantId`、`role==role`，并绑定到 structlog contextvars | `app/middleware/security.py::JWTMiddleware.__call__` / `_set_identity` · `tests/test_middleware.py::test_jwt_with_public_key_verifies_signature` |
| SC03-13 | 流式响应不被包装或截断 | 应用已启动；存在返回 `StreamingResponse`（`more_body=True`）的端点 | 请求该流式端点 | 200；body 为原始分片拼接（如 `"chunk-onechunk-two"`），无 `{"data": …}` 包裹、无截断 | `app/middleware/transform.py::TransformMiddleware.__call__` · `tests/test_middleware.py::test_streaming_response_not_wrapped_or_truncated` |

---

## SC04: 统一响应格式（`app/middleware/transform.py`）

`TransformMiddleware` 把成功的 2xx JSON 响应包装为 `{"data": …}`；错误响应、无 body 响应与非 JSON 响应直接透传。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC04-1 | 成功 JSON 响应包装为 data envelope | 端点返回 2xx `application/json` | 请求该端点 | 200；body == `{"data": <原响应体>}`，如 `{"hello":"world"}` → `{"data":{"hello":"world"}}` | `app/middleware/transform.py::_wrap` · `tests/test_middleware.py::test_success_response_wrapped_in_data` |
| SC04-2 | 4xx/5xx 错误响应不被二次包装 | 端点返回 4xx/5xx（全局异常处理器产出 `{"error": …}`） | 请求并检查 body | body 保持 `{"error":{"code":…,"message":…}}`，无 `data` 外层 | `app/middleware/transform.py::_should_wrap`（仅 2xx 才包）· `tests/test_errors.py` |
| SC04-3 | 204/304 无 body 响应不包装 | 端点返回 204 或 304 | 请求并检查 | 状态码不变，响应体为空，无 `{"data": null}` | `app/middleware/transform.py::_NO_BODY_STATUSES == {204, 304}` |
| SC04-4 | 非 JSON content-type 不包装 | 端点返回非 `application/json`（如 `text/plain`、`text/event-stream`） | 请求并检查 | 响应体原样透传，无 `data` 包裹 | `app/middleware/transform.py::_should_wrap`（content-type 需以 `application/json` 开头） |

---

## SC05: 异常处理（`app/errors.py`）

全局异常处理器把异常映射为稳定错误码（`_STATUS_CODE_MAP`），统一输出 `{"error": {code, message, …}}`。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC05-1 | 未处理异常返回 500 | 端点内部抛非 HTTP 异常（如 `RuntimeError`） | 请求该端点（`raise_server_exceptions=False`） | 500；body == `{"error":{"code":"INTERNAL_ERROR","message":"Internal server error"}}` | `app/errors.py::unhandled_exception_handler` · `tests/test_errors.py::test_unhandled_exception_returns_internal_error` |
| SC05-2 | HTTPException 404 返回结构化错误 | 端点抛 `HTTPException(404, "resource not found")` | 请求该端点 | 404；body == `{"error":{"code":"NOT_FOUND","message":"resource not found"}}` | `app/errors.py::http_exception_handler` · `tests/test_errors.py::test_http_exception_returns_structured_error` |
| SC05-3 | RequestValidationError 返回 422 | 端点存在 Pydantic body 校验，请求体缺必填字段 | POST 空 body | 422；`error.code=="VALIDATION_ERROR"`、`error.message=="Request validation failed"`、`error.details` 为 `exc.errors()` 列表 | `app/errors.py::validation_exception_handler` · `tests/test_errors.py::test_validation_error_returns_422` |

---

## SC06: 数据模型（`app/models/`）

契约级校验（无需真实数据库），通过 `import app.models` 将全部模型注册到 `Base.metadata` 后断言。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC06-1 | 18 张表全部注册 | 导入 `app.models` | `set(Base.metadata.tables)` 与 EXPECTED_TABLES 比较 | 相等，含 18 张表（users、tenants、agent_registry、run_logs、trace_*、task_outbox_events 等） | `app/models/__init__.py` · `tests/test_models.py::EXPECTED_TABLES` |
| SC06-2 | relationship mapper 配置无错误 | 导入 `app.models` | 调用 `sqlalchemy.orm.configure_mappers()` | 不抛异常（无 ambiguous/broken relationship） | `app/models/*.py` · `tests/test_models.py::test_mappers_configure_without_relationship_errors` |
| SC06-3 | UserMemory.embedding 为 pgvector Vector(1536) | 导入 `app.models` | 检查 `user_memories.embedding` 列类型 | `isinstance(type, Vector)` 且 `type.dim == 1536` | `app/models/user_memory.py::UserMemory.embedding` · `tests/test_models.py::test_user_memory_embedding_is_pgvector_1536` |
| SC06-4 | 唯一约束存在 | 导入 `app.models` | 检查各表约束名 | `uq_users_tenant_sso_sub`、`uq_users_tenant_email`、`uq_tenants_slug`、`uq_refresh_tokens_token_hash`、`uq_run_logs_trace_id` 均存在 | `app/models/user.py` / `tenant.py` / `refresh_token.py` / `run_log.py` · `tests/test_models.py::test_unique_constraints` |
| SC06-5 | 租户隔离索引存在 | 导入 `app.models` | 检查各表索引名 | `ix_users_tenant_id`、`ix_agent_registry_tenant_status`、`ix_run_logs_tenant_created_at` 均存在 | `app/models/user.py` / `agent.py` / `run_log.py` · `tests/test_models.py::test_tenant_scoped_indexes_present` |
| SC06-6 | 外键关联正确 | 导入 `app.models` | 检查各表外键 target | `users→tenants.id`、`agent_registry→tenants.id`、`tasks→users.id` 均存在 | `app/models/user.py` / `agent.py` / `task.py` · `tests/test_models.py::test_foreign_keys_wired` |
| SC06-7 | Check 约束存在 | 导入 `app.models` | 检查 CheckConstraint 名 | `ck_users_role`、`ck_tasks_status`、`ck_user_memories_ranges` 均存在 | `app/models/user.py` / `task.py` / `user_memory.py` · `tests/test_models.py::test_check_constraints_present` |
| SC06-8 | 集合关系使用 selectin lazy | 导入 `app.models` | 检查关系 `lazy` 属性 | `Tenant.users.property.lazy == "selectin"`、`User.run_logs.property.lazy == "selectin"` | `app/models/tenant.py` / `user.py` · `tests/test_models.py::test_collection_relationships_use_selectin` |

---

## SC07: Seed 脚本（`app/seed.py`）

Seed 自愈且幂等：每个必需记录独立检查、缺失才插入；事务级 advisory lock 序列化并发执行。运行方式：`python -m app.seed`（需先 `alembic upgrade head`）。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC07-1 | 创建默认租户 | 空库（或 tenants 无 `default`） | 执行 `seed(session)` | `Tenant(slug="default", name="Default Tenant", status="active")` 入库 | `app/seed.py::seed` · `DEFAULT_TENANT_SLUG` |
| SC07-2 | 创建 platform_admin 用户 | 默认租户已存在 | 执行 `seed(session)` | 存在 `User(email="admin@example.com", role="platform_admin", sso_sub="admin-local")`（email 受 `ADMIN_EMAIL` 环境变量覆盖） | `app/seed.py::seed` · `DEFAULT_ADMIN_EMAIL` |
| SC07-3 | 创建全局工具模板 | 空库 | 执行 `seed(session)` | `ToolRegistry` 含 `tool-template-http`、`tool-template-webhook`，且 `tenant_id IS NULL` | `app/seed.py::GLOBAL_TOOL_TEMPLATES` |
| SC07-4 | 幂等性 | 已完整 seed 一次 | 再次执行 `seed(session)` | 各记录不重复（tenant/admin/tool 各保持 1 条） | `app/seed.py::seed`（每条先查再插） |
| SC07-5 | 并发安全 | 两个并发 seed 会话 | 同时执行 `seed(session)` | `SELECT pg_advisory_xact_lock(hashtext('eap:seed'))` 序列化，commit/rollback 时释放，无重复插入 | `app/seed.py::seed` |

---

## SC08: 日志（`app/logging_conf.py`）

structlog 全局配置：JSON 输出到 stdout，敏感键脱敏，请求上下文绑定。

| # | 用例 | 前置条件 | 步骤 | 期望 | 对应代码 |
|---|------|---------|------|------|---------|
| SC08-1 | structlog JSON 输出到 stdout | `configure_logging(json_output=True)` | 触发一条日志并捕获 stdout | 输出为 JSON（`JSONRenderer`）；stdlib logger 经 `basicConfig(stream=sys.stdout)` 同流输出 | `app/logging_conf.py::configure_logging` |
| SC08-2 | 敏感字段脱敏 | 日志事件含敏感键 | 触发含 `authorization` / `password` / `token` / `api-key` / `secret` / `cookie` 的日志 | 对应值被替换为 `"[REDACTED]"`；`_normalise` 忽略大小写且把 `_` 归一化为 `-`（`api_key`、`API-KEY` 均命中） | `app/logging_conf.py::_redact_sensitive` · `_SENSITIVE_KEYS` |
| SC08-3 | request_id/tenant_id/user_id 上下文绑定 | 应用已启动 | 处理一个请求后检查访问日志 | `request_id`、`tenant_id`、`user_id` 均绑定在事件上下文中（`RequestContextMiddleware` 初始化 request_id，身份中间件经 `_set_identity` 覆写 tenant_id/user_id） | `app/middleware/request_context.py::RequestContextMiddleware` · `app/middleware/security.py::_set_identity` |
