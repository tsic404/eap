# 数据库迁移与部署

schema 变更由 Alembic 管理（`apps/backend/alembic/`），部署时执行 `alembic upgrade head`。
本节记录 schema 变更的部署注意事项。

## NOT VALID → VALIDATE 两阶段外键（`9c3a1f2b4d5e_tenant_scoped_composite_fks`）

该迁移为租户隔离引入复合外键。旧 schema 允许跨租户引用，已填充的库可能包含历史违规行；
直接创建普通外键会因存量数据违规而整体回滚。因此采用两阶段回滚安全策略：

1. **`ADD CONSTRAINT … NOT VALID`**：立即对新写入行生效（拦截新的跨租户行），但不扫描存量行。
2. **`ALTER TABLE … VALIDATE CONSTRAINT`**：扫描存量行，发现历史跨租户行则失败并指明具体表与约束名。

### 窗口期

阶段 1 与阶段 2 之间是「约束未验证」窗口：

- 约束是否已验证仅记录在 PostgreSQL 目录表 `pg_constraint.convalidated`，应用层不感知。
- 窗口期内约束已拦截新写入的跨租户行，但存量历史违规行尚未被扫描。
- 若 `VALIDATE` 阶段遇到历史跨租户行，将拒绝该迁移。

默认部署下两阶段在同一 Alembic 事务内完成，窗口极短且对外原子（要么全部生效，要么整体回滚）。
但若部署/回滚路径将两阶段拆分——例如先提交 `NOT VALID`、把 `VALIDATE` 延后为独立步骤——窗口会跨过
部署边界而持续存在，需按下述方式处理，避免约束长期停留在 `NOT VALID` 状态。

### 处理方式

**方式一：尽快重跑 `VALIDATE`（幂等）**

`ALTER TABLE … VALIDATE CONSTRAINT` 幂等：对已校验约束是廉价的空操作，对未校验约束执行存量扫描。
按部署路径分两种情形：

- **默认原子路径**（两阶段同事务）：`VALIDATE` 失败会回滚整个迁移事务，revision 未记录，
  修复孤儿行后重跑 `alembic upgrade head` 会重新执行整个迁移（含 `VALIDATE`）：

  ```bash
  cd apps/backend
  alembic upgrade head
  ```

- **拆分部署路径**（`NOT VALID` 已提交、revision 已标记为已应用）：数据库已处于 `head`，
  重跑 `alembic upgrade head` 是空操作，不会执行延后的 `VALIDATE`。须直接执行以下 8 条语句
  （与迁移文件 Phase 2 一致，幂等可反复执行）：

  ```sql
  ALTER TABLE agent_registry VALIDATE CONSTRAINT fk_agent_registry_tenant_created_by_users;
  ALTER TABLE tool_registry VALIDATE CONSTRAINT fk_tool_registry_tenant_created_by_users;
  ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_tenant_creator_id_users;
  ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_tenant_assignee_id_users;
  ALTER TABLE user_memories VALIDATE CONSTRAINT fk_user_memories_tenant_user_id_users;
  ALTER TABLE audit_logs VALIDATE CONSTRAINT fk_audit_logs_tenant_user_id_users;
  ALTER TABLE run_logs VALIDATE CONSTRAINT fk_run_logs_tenant_agent_id_agent_registry;
  ALTER TABLE run_logs VALIDATE CONSTRAINT fk_run_logs_tenant_user_id_users;
  ```

若 `VALIDATE` 因历史跨租户行失败，报错会指明表与约束名；按迁移文件 docstring 中的 SQL 修复孤儿行后，
重跑对应命令即可。

**方式二：独立部署后检查**

将 `VALIDATE` 拆为部署后检查任务，核对全部 8 条复合外键既存在且均已校验（`convalidated = t`）。
`conname` 不跨 schema 唯一，须限定 `contype = 'f'` 与 schema（`public`）：

1. **完整性检查**——把 8 条预期约束名与 `pg_constraint` 中实际存在的同名外键做差集，
   缺失约束在此列出，期望结果为空：

   ```sql
   SELECT unnest(ARRAY[
       'fk_agent_registry_tenant_created_by_users',
       'fk_tool_registry_tenant_created_by_users',
       'fk_tasks_tenant_creator_id_users',
       'fk_tasks_tenant_assignee_id_users',
       'fk_user_memories_tenant_user_id_users',
       'fk_audit_logs_tenant_user_id_users',
       'fk_run_logs_tenant_agent_id_agent_registry',
       'fk_run_logs_tenant_user_id_users'
   ]) AS missing_constraint
   EXCEPT
   SELECT conname
   FROM pg_constraint
   WHERE contype = 'f'
     AND connamespace = 'public'::regnamespace;
   ```

2. **校验状态检查**——每条均应 `convalidated = t`，任一为 `f` 即告警：

   ```sql
   SELECT conrelid::regclass AS table_name,
          conname,
          convalidated
   FROM pg_constraint
   WHERE contype = 'f'
     AND connamespace = 'public'::regnamespace
     AND conname IN ('fk_agent_registry_tenant_created_by_users',
                     'fk_tool_registry_tenant_created_by_users',
                     'fk_tasks_tenant_creator_id_users',
                     'fk_tasks_tenant_assignee_id_users',
                     'fk_user_memories_tenant_user_id_users',
                     'fk_audit_logs_tenant_user_id_users',
                     'fk_run_logs_tenant_agent_id_agent_registry',
                     'fk_run_logs_tenant_user_id_users')
   ORDER BY conname;
   ```

两步同时通过（差集为空且全部 `convalidated = t`）才表示约束集合完整且已校验。

## Weaviate 向量库升级（1.24.x → 1.27.x）

dev 与 prod compose 均固定 `semitechnologies/weaviate:1.27.27`。Dify 1.17 内置的
`weaviate-client` 在连接时做版本门禁（`weaviate/connect/v4.py`），拒绝低于 `1.27.0`
的服务端；旧固定值 `1.24.10` 会让文档索引必然失败
（`GET /api/knowledge-bases/{kb_id}/documents/{document_id}/status` 恒为 `failed`）。

下文命令以 dev 为例（`docker-compose.yml`，project 名 `eap`，卷 `eap_weaviadata`）；
prod 每条命令都带 `-f docker-compose.prod.yml`，project 名换成 `eap-prod`
（`docker-compose.prod.yml` 首行 `name: eap-prod`），卷换成 `eap-prod_weaviadata`。

### 存量部署升级步骤

0. **先备份向量卷。** 本仓 weaviate 未启用 backup 模块，用卷级打包（`stop` 同时保证刷盘后
   数据一致；回滚 = 把 tar 解回同名卷后换回旧镜像）：

   ```bash
   docker compose stop weaviate
   # prod：卷名为 eap-prod_weaviadata，stop/start 都带 -f docker-compose.prod.yml
   docker run --rm -v eap_weaviadata:/data alpine tar cz -C /data . > weaviate-$(date +%F).tgz
   docker compose start weaviate
   ```

1. 停旧容器用优雅停止（`docker compose stop weaviate`，或直接 `docker compose up -d`
   由 compose 发 SIGTERM），不要用 `docker rm -f`：未刷盘的 WAL 尾段不会被恢复。
2. 重建容器并确认版本：`docker compose up -d` → `docker compose ps` 中 weaviate healthy，
   version ≥ `1.27.0`。读取要在 compose 网络内并带 API key——weaviate 未映射宿主端口，
   且关闭了匿名访问（`AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "false"`），宿主机直连
   `:8080` 会连不上或 401：

   ```bash
   docker compose exec -T weaviate sh -c \
     'wget -qO- --header="Authorization: Bearer $AUTHENTICATION_APIKEY_ALLOWED_KEYS" http://127.0.0.1:8080/v1/meta'
   ```

3. 校验存量数据：同一条命令把 `/v1/meta` 换成 `/v1/schema`，应仍列出原有 collection；
   再对同一知识库跑一次召回测试，确认仍能命中既有分块。

Docker 单节点部署下 Raft 元数据与旧 schema 迁移是自动的；实测 1.24.10 → 1.27.27 直跳后
collection、对象与向量检索均保持可用。Weaviate 官方仍建议逐个小版本升级
（1.24 → 1.25 → 1.26 → 1.27，每档取最新 patch）。

### 向量数据不可信时重建

升级前索引一直失败的部署，向量库里可能没有可用数据（collection 已建但无对象）。此时清空向量卷重建：

```bash
# dev（project 名 eap）
docker compose down                       # 不带 -v：postgres / dify 数据必须保留
docker volume rm eap_weaviadata
docker compose up -d

# prod（project 名 eap-prod）
docker compose -f docker-compose.prod.yml down
docker volume rm eap-prod_weaviadata
docker compose -f docker-compose.prod.yml up -d
```

知识库与文档元数据存放在 Dify 的 postgres（`datasets` / `documents`），不受向量卷重建影响；
之后对 `failed` / `indexing` 状态的文档触发重试或重新上传，索引会在首次写入时按需重建 collection。
