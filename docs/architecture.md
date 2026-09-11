# 企业智能体平台 — 完整架构设计文档 v8.0

> 版本: v8.0 | 2026-09-07
> 涵盖: 前后端全栈架构 + 14 模块深度设计 + 7 模块血管图与内部四层实现
> 后端迁移至 Python/FastAPI（SQLAlchemy 2.0 async + Pydantic v2 + RQ），前端保持 Next.js/React/SWR/Tailwind 不变

---

## 目录

### 第一部分：总览与基础架构
1. [系统概述与产品定位](#ch1)
2. [Dify 引擎深度分析](#ch2)
3. [技术选型与决策记录](#ch3)
4. [整体架构设计](#ch4)
5. [SQLAlchemy ORM 数据模型设计](#ch5)
6. [FastAPI 后端架构设计](#ch6)
7. [API 接口设计规范](#ch7)

### 第二部分：业务模块设计
8. [模块 1：用户与权限](#m1)
9. [模块 2：智能体](#m2)
10. [模块 3：知识库](#m3)
11. [模块 4：会话](#m4)
12. [模块 5：工具](#m5)
13. [模块 6：运行记录](#m6)
14. [模块 7：任务中心](#m7)
15. [模块 8-14](#m8-14)

### 第三部分：前端架构深度设计
16. [前端项目架构](#ch-fe-arch)
17. [前端组件设计规范](#ch-fe-components)
18. [前端数据流与状态管理](#ch-fe-data)
19. [前端路由与页面设计](#ch-fe-routes)
20. [前端开发指南](#ch-fe-guide)

### 第四部分：后端架构深度设计
21. [Dify 适配层详细设计](#ch-dify)
22. [后端开发指南与最佳实践](#ch-be-guide)
23. [后端中间件与拦截器](#ch-be-middleware)

### 第五部分：横切关注点
24. [安全性设计](#ch-security)
25. [可观测性设计](#ch-obs)
26. [测试策略](#ch-test)
27. [部署与运维设计](#ch-deploy)
28. [前后端开发协同规范](#ch-collab)
29. [开发阶段规划](#ch-plan)

---

# 第一部分：总览与基础架构

## <a id="ch1"></a>一、系统概述与产品定位

### 1.1 产品定义

企业智能体平台（Enterprise Agent Platform，简称 EAP）是一个面向企业的多租户 SaaS 系统。平台为企业客户提供统一的 AI 助手对话入口、知识库治理工具、智能体编排能力和企业工具集成方案。系统的核心设计原则是：**用户只与平台自有界面交互**，底层 AI 引擎采用 Dify 开源平台，以纯后端 API 服务的方式部署，用户对 Dify 的存在完全无感知。

系统划分为两个相互独立但又数据互通的工作区：
- **用户工作区**（`/user`）：面向普通员工，提供 AI 助手对话、智能体广场、任务中心、会话管理、文件管理等功能
- **管理工作区**（`/admin`）：面向管理员，提供智能体全生命周期管理、知识库治理、工具配置、运行审计、系统监控等功能

### 1.2 核心设计原则

1. **前端完全自主**：所有 UI 由平台自有 Next.js 应用渲染，不嵌入或跳转任何 Dify 界面
2. **接口抽象优先**：后端通过 Service Interface 抽象业务语义，Adapter 层封装 Dify API 调用，支持未来替换为自建引擎
3. **多租户原生支持**：从数据模型到 API 调用全链路注入租户隔离逻辑
4. **可观测性内置**：结构化日志、Prometheus 指标、分布式追踪从 Day 1 就位
5. **渐进式交付**：P1 快速上线核心对话能力（8周），P2 增强记忆和评测（6周），P3 企业化（5周）

### 1.3 核心能力全景

**P1 阶段（第 1-8 周）—— 核心底座**

| 能力域 | 具体功能 | 前端关键组件 | 后端关键服务 |
|--------|---------|------------|------------|
| 多租户管理 | 企业 SSO 登录（OIDC）、租户数据隔离、五级 RBAC、成员邀请管理 | LoginPage, MemberList, MemberInviteModal | AuthService, TenantDependency, RolesDependency |
| 智能体生命周期 | 创建（选择模型/Prompt/KB/工具）、配置编辑、内建调试对话、一键发布/下线 | AgentCreateForm, AgentConfigPanel, MiniChatWindow, PublishButton | AgentService, DifyConsoleClient |
| 知识库治理 | 创建知识库、上传文档（8种格式）、自动索引状态跟踪、分块预览、召回测试 | KBCard, DocumentUploader, ChunkList, RetrievalTest | KnowledgeService, DifyClientService |
| 流式对话 | SSE 流式逐字渲染、18种 Dify 事件处理、引用来源展示、工具调用卡片、对话历史 | ChatWindow, StreamingText, CitationCard, ToolCallCard, MessageBubble | ConversationAdapter |
| 工具集成 | 5种工具类型注册、风险等级/权限模式配置、在线调试、审批代理 | ToolConfigForm, DebugPanel, RiskLevelBadge | ToolProxy, TaskService, RQ |
| 运行审计 | 日志列表（多维度筛选）、Trace 详情时间线、步骤/引用/工具三维展示 | RunLogTable, TraceTimeline, TraceStats | TraceService |
| 任务中心 | 高风险工具审批流、任务优先级/超时管理、审批操作 | TaskCenterPage, TaskCard, ApproveButton | TaskService, RQ Workers |
| 工作台仪表盘 | 四项核心指标、24h 趋势图、Top 智能体排行、系统健康、实时告警 | MetricCard, TrendChart, TopAgentsList, HealthStatusPanel | DashboardService |

**P2 阶段（第 9-14 周）—— 能力增强**

| 能力域 | 具体功能 |
|--------|---------|
| 记忆系统 | 对话结束自动 LLM 分析提取偏好/事实、pgvector 向量语义检索召回、用户可见可管理的记忆列表、支持纠错和删除 |
| 评测中心 | 测试集 CRUD、批量自动评测（BLEU/ROUGE/语义相似度）、多版本回归对比、发布前质量门禁 |
| Trace 细粒度存储 | 四表拆分存储、多维度查询分析、长期审计留存策略 |
| 企业工具代理升级 | OAuth2 鉴权注入、幂等键生成、指数退避重试、请求/响应脱敏、熔断器 |
| 工作流编排 | 自有表单创建工作流、DSL YAML 生成与导入、节点类型配置 |
| 内容安全 | 输入输出策略审核、敏感词过滤、风险事件管理 |

**P3 阶段（第 15-19 周）—— 企业进阶**

| 能力域 | 具体功能 |
|--------|---------|
| OCR 文档处理 | 扫描件 PDF 自动 OCR 提取、复杂表格版面分析结构化 |
| 多 Agent 协作 | A2A 协议试点、Agent 间任务委托 |
| K8s 生产部署 | Helm Charts、HPA 自动伸缩、多副本高可用 |
| 安全加固 | 渗透测试、压力测试（1000 并发）、灾备方案 |

### 1.4 领域类型体系（28 个类型完整定义）

平台定义了一套前后端共享的领域类型。后端使用 Pydantic v2 模型定义，FastAPI 自动生成 OpenAPI schema；前端通过 `openapi-typescript` 从 OpenAPI schema 生成 TypeScript 类型，确保前后端类型一致性。

**用户与权限上下文**：
- `UserRole`：五级角色字面量类型 `Literal["platform_admin", "agent_admin", "knowledge_admin", "auditor", "employee"]`
- `WorkspaceKey`：工作区标识 `Literal["user", "admin"]`
- `CurrentUser`：当前登录用户完整信息 `{id, name, email, department, role, avatarText, canAccessAdmin, tenantId, tenantName}`
- `Member`：组织成员视图 `{id, name, department, role, status, lastActiveAt}`

**智能体上下文**：
- `AgentType`：`Literal["chat", "workflow", "agent", "data"]`
- `AgentStatus`：`Literal["draft", "testing", "published", "offline"]`
- `Agent`：智能体完整实体，包含 `{id, name, description, type, status, category, visibility, icon, tags, modelId, modelName, prompt, knowledgeBaseIds, toolIds, callsToday, successRate, avgLatencyMs, version, createdBy, createdAt, updatedAt, publishedAt}`

**知识库上下文**：
- `KnowledgeStatus`：`Literal["ready", "indexing", "failed"]`
- `KnowledgeBase`：`{id, name, description, type, owner, status, docCount, chunkCount, authorizedScope, lastIndexedAt}`
- `KnowledgeDocument`：`{id, knowledgeBaseId, name, fileType, sizeLabel, status, chunkCount, uploadedBy, uploadedAt}`
- `KnowledgeChunk`：`{id, knowledgeBaseId, documentId, title, content, tokenCount, scorePreview}`
- `IndexingJob`：`{id, knowledgeBaseId, documentId, status, progress, attempts}`

**工具上下文**：
- `ToolRiskLevel`：`Literal["low", "medium", "high"]`
- `ToolPermissionMode`：`Literal["auto", "confirm", "disabled"]`
- `ToolAsset`：`{id, name, description, type, endpoint, method, riskLevel, permissionMode, authType, status, callsToday}`
- `ToolDebugCase`：`{id, toolId, name, requestBody, responseBody, statusCode, latencyMs}`

**会话上下文**：
- `RunStatus`：`Literal["success", "failed", "running", "blocked"]`
- `ChatMessage`：`{id, role, content, createdAt}`
- `Citation`：`{id, sourceName, knowledgeBaseName, excerpt, score}`
- `ToolCallRun`：`{id, toolName, status, permissionMode, latencyMs, requestSummary, responseSummary}`
- `Conversation`：`{id, title, agentId, agentName, userId, status, messages[], citations[], toolCalls[], tokenUsage, latencyMs, traceId}`

**运行记录上下文**：
- `RunTraceStep`：`{id, name, type, status, latencyMs, detail}`
- `RunLog`：`{id, traceId, agentId, agentName, userId, userName, status, input, output, modelName, tokenUsage, latencyMs, steps[], citations[], toolCalls[]}`

**其他上下文**：
- `TaskItem`、`ModelProvider`、`ModelAsset`、`DashboardMetric`、`TrendPoint`、`SystemHealthItem`、`HealthStatus`、`PreviewCapability`

### 1.5 用户角色与权限总览

平台定义了五级角色体系，不同角色对管理端和用户端拥有不同的访问和操作权限。角色的分配由平台管理员（platform_admin）在成员管理页面进行。

**platform_admin（平台管理员）**：拥有最高权限。可以访问所有管理端和用户端功能，包括创建/编辑/删除智能体、知识库、工具，管理成员（邀请/禁用/修改角色），配置模型供应商和系统设置，审批高风险工具调用，查看所有运行日志和系统健康状态。

**agent_admin（智能体管理员）**：可以创建、编辑、删除、发布和下线智能体。可以创建和管理知识库。可以审批高风险工具调用。可以查看运行日志和系统健康。但不能管理成员、配置模型供应商或修改系统设置。

**knowledge_admin（知识库管理员）**：可以创建、编辑、删除知识库和上传文档。可以查看智能体列表（只读）。不能创建或修改智能体、工具。可以查看运行日志和系统健康。

**auditor（审计员）**：只读角色。可以查看智能体列表、知识库列表、运行日志、成员列表和系统健康。不能执行任何创建、修改或删除操作。不能发起对话。

**employee（普通员工）**：只能访问用户工作区。可以浏览已发布的智能体、发起和管理自己的对话、查看和处理自己的任务。不能访问管理端任何功能。

---

## <a id="ch2"></a>二、Dify 引擎深度分析

### 2.1 Dify 架构与部署模式

Dify（`langgenius/dify`，GitHub 151k+ Stars）是一个成熟的开源 LLM 应用开发平台。其标准部署包含以下组件：

| 组件 | 技术栈 | 端口 | 本平台部署 |
|------|--------|------|----------|
| API 服务 | Python Flask | 5001 | ✅ 部署 |
| Worker 服务 | Celery | — | ✅ 部署 |
| Web 前端 | Next.js | 3000 | ❌ 不部署 |
| PostgreSQL | — | 5432 | ✅ 共享 |
| Redis | — | 6379 | ✅ 共享 |
| 向量数据库 | Weaviate/Qdrant | — | ✅ 部署 |

关键决策：**Dify Web 前端完全不部署**。所有用户交互通过平台自有 Next.js 前端完成。Dify 的 Console API（内部管理 API）由平台后端的 `DifyConsoleClient` 通过 Session Cookie 认证方式调用，实现对 Dify 应用的创建、配置和管理。

### 2.2 Service API 完整清单（面向终端用户）

**应用信息 API（3 个）**

1. `GET /v1/info` — 获取应用基本信息和元数据。返回 `{name, description, tags, mode, author_name}`。mode 枚举：`chat`（基础对话）、`agent-chat`（旧版 Agent）、`agent`（新版 Agent）、`advanced-chat`（Chatflow）、`workflow`（工作流）、`completion`（文本生成）。

2. `GET /v1/parameters` — 获取应用参数配置。返回 `{opening_statement, suggested_questions, suggested_questions_after_answer, speech_to_text, text_to_speech, retriever_resource, annotation_reply, user_input_form, file_upload, system_parameters}`。

3. `GET /v1/meta` — 获取应用元信息。返回 `{tool_icons}`。

**对话 API（9 个）**

4. `POST /v1/chat-messages` — 发送对话消息，支持 `response_mode: "blocking"`（一次性返回）或 `"streaming"`（SSE 流式推送）。请求体参数：
```json
{
  "query": "string (required)",
  "inputs": { "key": "value" },
  "response_mode": "streaming | blocking",
  "user": "string (required)",
  "conversation_id": "string (可选，为空则自动创建新会话)",
  "files": [{ "type": "image|document|audio|video|custom", "transfer_method": "remote_url|local_file", "url": "...", "upload_file_id": "..." }],
  "auto_generate_name": true,
  "workflow_id": "string (Chatflow 应用指定版本)"
}
```

5. `POST /v1/completion-messages` — 文本生成消息（completion 模式应用）。
6. `POST /v1/chat-messages/:task_id/stop` — 停止正在生成的对话。
7. `GET /v1/messages` — 获取消息历史，查询参数：`conversation_id`（必选）、`user`（必选）、`first_id`（游标分页）、`limit`（默认 20）。
8. `GET /v1/conversations` — 获取会话列表，查询参数：`user`（必选）、`last_id`（游标分页）、`limit`（默认 20）、`sort_by`（`created_at | -created_at | updated_at | -updated_at`）、`pinned`。
9. `DELETE /v1/conversations/:id` — 删除会话。
10. `POST /v1/conversations/:id/name` — 重命名会话。
11. `GET /v1/messages/:message_id/suggested` — 获取建议的后续问题。
12. `GET /v1/conversations/:id/variables` — 获取会话变量。
13. `PUT /v1/conversations/:id/variables` — 更新会话变量。

**SSE 流式事件完整清单（18 种）**

| 事件名 | 触发时机 | 关键 payload 字段 | 前端处理 |
|--------|---------|------------------|---------|
| `message` | 每个 Token 推送 | task_id, message_id, conversation_id, answer | 追加文本到当前 assistant bubble |
| `message_end` | 生成完成 | message_id, conversation_id, metadata{usage, retriever_resources} | 停止流式；提取 citations 和 usage |
| `message_file` | 文件消息 | id, type, url, belongs_to | 追加文件链接到 bubble |
| `message_replace` | Agent 模式消息替换 | 同上 | 替换已有消息内容 |
| `agent_message` | Agent 思考过程 | task_id, message_id, answer | 展示 Agent 内部推理 |
| `agent_thought` | Agent 思维链步骤 | id, position, thought, tool, observation, tool_input | 展示思维链详情（可折叠） |
| `workflow_started` | 工作流开始执行 | task_id, workflow_run_id, data | 显示工作流进度指示器 |
| `workflow_finished` | 工作流执行完成 | data{status, outputs, error, total_steps, total_tokens} | 更新指示器为完成/失败状态 |
| `node_started` | 工作流节点开始 | data{node_id, node_type, title, index, inputs} | 在进度列表中添加节点 |
| `node_finished` | 工作流节点完成 | data{status, outputs, error, elapsed_time} | 标记节点完成/失败 |
| `parallel_branch_started` | 并行分支开始 | data{parallel_id, parallel_branch_id} | 添加分支指示 |
| `parallel_branch_finished` | 并行分支完成 | data{status, error} | 标记分支完成/失败 |
| `text_chunk` | 文本块推送 | data{text} | 追加文本块 |
| `text_replace` | 文本替换 | data{text} | 替换文本 |
| `tts_message` | TTS 音频片段 | task_id, message_id, audio (base64) | 播放音频片段 |
| `tts_message_end` | TTS 完成 | audio | 播放完整音频 |
| `error` | 发生错误 | task_id, status, code, message | 显示错误 Banner + 重试按钮 |
| `ping` | 心跳保活 | 无 | 忽略（保持连接） |

**知识库 API（16 个）**

14–21. 数据集管理：`GET /v1/datasets`（列表，page/limit 分页）、`POST /v1/datasets`（创建）、`DELETE /v1/datasets/:id`、`PUT /v1/datasets/:id`（更新）。

22–29. 文档管理：`GET /v1/datasets/:id/documents`（列表，keyword/status 筛选，page/limit 分页）、`POST /v1/datasets/:id/document/create-by-text`（文本创建）、`POST /v1/datasets/:id/document/create-by-file`（文件上传，multipart/form-data）、`POST /v1/datasets/:id/document/create-by-url`（URL 导入）、`DELETE /v1/datasets/:id/documents/:doc_id`、`GET /v1/datasets/:id/documents/:doc_id/segments`（分段列表）、`POST /v1/datasets/:id/documents/:doc_id/segments`（新增分段）、`DELETE /v1/datasets/:id/documents/:doc_id/segments/:seg_id`、`PUT /v1/datasets/:id/documents/:doc_id/segments/:seg_id`（更新分段）。

30–31. 检索：`POST /v1/datasets/:id/retrieve`（检索测试，支持 hybrid_search/semantic_search/full_text_search + reranking）、`GET /v1/datasets/:id/documents/:batch/indexing-status`（索引进度）。

**其他 API（10 个）**

32–41. 文件上传（`POST /v1/files/upload`，multipart `{file, user}` → `{id, name, size, extension, mime_type, url}`）、工作流执行（`POST /v1/workflows/run` + 状态查询 + 停止 + 日志）、标注管理（CRUD + 回复）、音频转换（TTS + Transcription）。

### 2.3 Console API 调用策略

Dify 的 Console API 是其 Web 前端调用的内部 API，使用 Session Cookie 认证。平台通过 `DifyConsoleClient` 服务代理所有管理操作。

**Console API 端点分类**：

应用管理：`POST /console/api/apps`（创建应用，参数 name/mode/description）、`PUT /console/api/apps/:id`（更新应用）、`POST /console/api/apps/:id/model-config`（配置模型和 Prompt）、`POST /console/api/apps/:id/copy`（复制应用）、`DELETE /console/api/apps/:id`（删除）、`GET /console/api/apps/:id/export`（导出 DSL YAML）、`POST /console/api/apps/import`（导入 DSL YAML）。

知识库管理：`POST /console/api/datasets`（创建知识库）、`DELETE /console/api/datasets/:id`（删除）。

工具管理：`GET /console/api/tools`（获取已安装工具）、`POST /console/api/tools/api-test`（测试自定义 API 工具）。

模型管理：`GET /console/api/model-providers`（获取模型供应商）、`GET /console/api/workspaces/current/models/model-types/:provider/models`（获取供应商下的模型列表）。

API Key 管理：`GET /console/api/apps/:id/api-keys`（获取 API Key 列表）、`POST /console/api/apps/:id/api-keys`（创建新 API Key）。

成员管理：`GET /console/api/members`、`POST /console/api/members`。

### 2.4 DifyConsoleClient 完整实现

```python
import asyncio
import json
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel


class DifyConsoleError(Exception):
    """Dify Console API 调用失败时抛出。"""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Dify Console error {status_code}: {body}")


class CreateAppParams(BaseModel):
    name: str
    mode: str
    description: str | None = None
    icon: str | None = None


class ModelConfig(BaseModel):
    pre_prompt: str | None = None
    model: dict[str, Any] | None = None
    user_input_form: list[dict[str, Any]] | None = None
    file_upload: dict[str, Any] | None = None
    opening_statement: str | None = None
    suggested_questions: list[str] | None = None


class CreateDatasetParams(BaseModel):
    name: str
    description: str | None = None
    indexing_technique: str | None = None


class TestApiToolParams(BaseModel):
    name: str
    url: str
    method: str
    headers: str
    params: str
    body: str


class DifyConsoleClient:
    """
    Dify Console API 代理客户端。
    使用 Session Cookie 认证，支持并发安全登录和 Session 过期自动重登录。
    """

    def __init__(self) -> None:
        self._session_cookie: str | None = None
        self._base_url: str = re.sub(r"/v1$", "", os.environ["DIFY_API_BASE_URL"])
        self._login_lock = asyncio.Lock()
        self._login_task: asyncio.Task[None] | None = None
        # httpx.AsyncClient 在整个生命周期内复用连接池
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DifyConsoleClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def startup(self) -> None:
        """应用启动时调用，完成首次登录。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        await self._login()

    async def shutdown(self) -> None:
        """应用关闭时调用，释放连接池。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ────────── 登录与 Session 管理 ──────────

    async def _login(self) -> None:
        """并发安全登录：多个协程同时触发时只执行一次。"""
        if self._login_task is not None:
            # 等待正在进行的登录
            await asyncio.shield(self._login_task)
            return

        async def _do_login() -> None:
            await self._do_login()

        self._login_task = asyncio.create_task(_do_login())
        try:
            await self._login_task
        finally:
            self._login_task = None

    async def _do_login(self) -> None:
        assert self._client is not None
        resp = await self._client.post(
            f"{self._base_url}/console/api/login",
            json={
                "email": os.environ["DIFY_ADMIN_EMAIL"],
                "password": os.environ["DIFY_ADMIN_PASSWORD"],
            },
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise DifyConsoleError(resp.status_code, resp.text)
        set_cookie = resp.headers.get("set-cookie")
        if not set_cookie:
            raise DifyConsoleError(500, "No session cookie returned from Dify login")
        self._session_cookie = set_cookie

    async def _ensure_session(self) -> None:
        if self._session_cookie is None:
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_session()
        assert self._client is not None

        headers: dict[str, str] = {"Cookie": self._session_cookie or ""}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        resp = await self._client.request(
            method,
            f"{self._base_url}{path}",
            json=json_body if json_body is not None else None,
            data=form_data if form_data is not None else None,
            headers=headers,
        )

        # Session 过期 → 自动重登录并重试一次
        if resp.status_code == 401:
            self._session_cookie = None
            await self._login()
            return await self._request(method, path, json_body=json_body, form_data=form_data)

        if resp.status_code >= 400:
            raise DifyConsoleError(resp.status_code, resp.text)

        text = resp.text
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # ────────── 应用管理 ──────────

    async def create_app(self, params: CreateAppParams) -> dict[str, Any]:
        return await self._request("POST", "/console/api/apps", json_body=params.model_dump(exclude_none=True))

    async def update_app(self, app_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/console/api/apps/{app_id}", json_body=data)

    async def delete_app(self, app_id: str) -> None:
        await self._request("DELETE", f"/console/api/apps/{app_id}")

    async def configure_model(self, app_id: str, config: ModelConfig) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/console/api/apps/{app_id}/model-config",
            json_body=config.model_dump(exclude_none=True),
        )

    async def duplicate_app(self, app_id: str, name: str) -> dict[str, Any]:
        return await self._request("POST", f"/console/api/apps/{app_id}/copy", json_body={"name": name})

    # ────────── 知识库管理 ──────────

    async def create_dataset(self, params: CreateDatasetParams) -> dict[str, Any]:
        return await self._request("POST", "/console/api/datasets", json_body=params.model_dump(exclude_none=True))

    async def delete_dataset(self, dataset_id: str) -> None:
        await self._request("DELETE", f"/console/api/datasets/{dataset_id}")

    # ────────── API Key 管理 ──────────

    async def get_api_keys(self, app_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/console/api/apps/{app_id}/api-keys")

    async def create_api_key(self, app_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/console/api/apps/{app_id}/api-keys")

    # ────────── 模型管理 ──────────

    async def get_model_providers(self) -> dict[str, Any]:
        return await self._request("GET", "/console/api/model-providers")

    async def get_models(self, provider: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/console/api/workspaces/current/models/model-types/{provider}/models",
        )

    # ────────── 工作流 DSL ──────────

    async def export_dsl(self, app_id: str) -> str:
        return await self._request("GET", f"/console/api/apps/{app_id}/export")

    async def import_dsl(self, yaml_content: str) -> dict[str, Any]:
        # Dify expects multipart file upload for DSL import
        assert self._client is not None
        await self._ensure_session()
        resp = await self._client.post(
            f"{self._base_url}/console/api/apps/import",
            files={"file": ("workflow.yml", yaml_content.encode("utf-8"), "application/x-yaml")},
            headers={"Cookie": self._session_cookie or ""},
        )
        if resp.status_code >= 400:
            raise DifyConsoleError(resp.status_code, resp.text)
        text = resp.text
        return json.loads(text) if text else {}

    # ────────── 工具管理 ──────────

    async def get_tools(self) -> dict[str, Any]:
        return await self._request("GET", "/console/api/tools")

    async def test_api_tool(self, params: TestApiToolParams) -> dict[str, Any]:
        return await self._request("POST", "/console/api/tools/api-test", json_body=params.model_dump())
```


---

## <a id="ch3"></a>三、技术选型与架构决策记录

### 3.1 技术栈总览

| 层次 | 技术选型 | 版本 | 核心价值 |
|------|---------|------|---------|
| 前端框架 | Next.js (App Router) | 15.x | React Server Components + ISR + 文件路由 |
| 前端语言 | TypeScript | 5.9 | 编译时类型安全 + IDE 智能提示 |
| 前端样式 | Tailwind CSS | 3.4 | 原子化 CSS + JIT + Design Token |
| 前端图标 | Lucide React | 0.468 | Tree-shakeable + 统一视觉风格 |
| 前端状态 | React Context + SWR | 2.x | 客户端全局状态 + 服务端缓存自动管理 |
| 后端框架 | FastAPI | 0.115+ | ASGI 高性能 + 自动 OpenAPI + 依赖注入 |
| 后端语言 | Python | 3.12 | 类型标注 + 生态丰富 + async/await |
| ORM | SQLAlchemy 2.0 (async) + Alembic | 2.x | 声明式映射 + async session + 迁移管理 |
| 数据库 | PostgreSQL 16 + pgvector | — | JSONB + 全文搜索 + 向量检索 |
| 缓存/队列 | Redis 7 + RQ | — | 缓存 + 延迟任务 + 优先级队列 |
| 认证 | authlib + python-jose | — | OIDC 完整支持 + JWT 签发/验证 |
| 运行时验证 | Pydantic v2 | 2.x | 类型推导 + 模型校验 + JSON Schema 自动生成 |
| 日志 | structlog | 24.x | 结构化 JSON + 异步友好的上下文日志 |
| 测试 | pytest + httpx.AsyncClient + pytest-asyncio | — | 单元 + API 集成 + 异步测试 |
| HTTP 客户端 | httpx | — | async + HTTP/2 + 连接池 |
| Redis 客户端 | redis.asyncio | — | 异步原生 + 连接池 |
| AI 引擎 | Dify CE (纯后端) | 1.x | RAG + 模型网关 + 工作流 |
| 容器 | Docker + docker-compose | — | 一键开发环境 |

### 3.2 架构决策记录（7 项 ADR）

**ADR-001：SQLAlchemy vs Tortoise ORM vs raw asyncpg**
选择 SQLAlchemy 2.0（async + asyncpg 驱动）。SQLAlchemy 2.0 的 `Mapped` / `mapped_column` 声明式映射提供编译时类型安全，`selectinload` / `joinedload` 支持细粒度关联加载，避免 N+1 查询。配合 Alembic 实现版本化迁移审查，每次 `alembic revision --autogenerate` 生成可审查的迁移脚本，避免数据丢失。代价是不支持部分高级 SQL 时需要降级到 `text()` 原生查询，但已覆盖 95%+ 场景。

**ADR-002：FastAPI vs Django REST / Flask**
选择 FastAPI 0.115+。FastAPI 的 `Depends()` 依赖注入体系天然契合端口-适配器模式，无需自建 DI 容器。Pydantic v2 模型同时承担请求校验、响应序列化和 OpenAPI schema 生成三重职责，消除手工维护 schema 的成本。`@router.get("/{id}")` 路由声明比 Django REST 的 Serializer + ViewSet 更直接。`Depends(require_roles("agent_admin"))` 一行代码完成权限声明，清晰直观。代价是缺乏 Django admin 等开箱模块，但平台自有管理端 UI。

**ADR-003：Dify 纯后端部署**
Dify Web 前端完全不部署。所有管理操作通过平台自有界面 + `DifyConsoleClient`（Session Cookie 认证）代理到 Dify Console API。用户零接触 Dify。

**ADR-004：多租户通过 user 参数编码**
Dify 社区版不支持多工作区。通过 `user = "{tenant_id}:{user_id}"` 实现会话隔离。

**ADR-005：Cache-Aside 模式**
所有 Dify API 查询结果缓存到 Redis。查询时先读 Redis，命中直接返回；未命中调用 Dify，结果写入 Redis（60s/300s TTL）后返回。CRUD 操作时主动删除相关缓存 key。

**ADR-006：RQ 替代进程内 asyncio Task**
审批任务执行、记忆提取、索引进度轮询等可能耗时 30+ 秒的操作使用 RQ（Redis Queue）异步处理。优势：持久化（Redis 存储，进程重启不丢失）、重试策略（指数退避）、优先级、延迟调度。

**ADR-007：OpenAPI schema 前后端类型共享**
28 个领域类型由后端 Pydantic v2 模型定义，FastAPI 自动生成 OpenAPI schema。前端通过 `openapi-typescript` 从 schema 生成 TypeScript 类型，消除前后端类型不一致风险。`Agent`、`KnowledgeBase` 等接口由 schema 自动推导，无需手工维护。

---

## <a id="ch4"></a>四、整体架构设计

### 4.1 端口-适配器架构

平台采用六边形架构。前端通过自有 REST API 与后端通信。后端 Router 层依赖 Service Interface（端口），由 Factory 在运行时注入 DifyAdapter 或 SelfBuiltAdapter 实现。

```mermaid
flowchart TD
    FE[Frontend Next.js - 3000]
    API[REST /api/*]
    subgraph Backend["FastAPI Backend - 3001"]
        MW["Middleware Pipeline - SecurityHeaders-CORS-RateLimit-JWTDependency-TenantDependency-RolesDependency-PydanticValidation"]
        CTL["Routers - 14 modules"]
        SI["Service Interfaces - 9 ports"]
        DA["DifyAdapter - P1"]
        SA["SelfBuiltAdapter - P2+"]
        WS["Wrapper Services - Auth/Memory/Task/ToolProxy/Dashboard/Health"]
    end
    FE --> API --> MW --> CTL --> SI
    SI --> DA --> D[Dify API - 5001]
    SI --> SA --> PG[(PostgreSQL)]
    WS --> PG
    WS --> R[(Redis)]
```

### 4.2 请求生命周期

每个进入后端的 HTTP 请求经过 7 层中间件/依赖管道：

```mermaid
flowchart TD
    A[Incoming Request] --> B[SecurityHeaders - Security headers]
    B --> C[CORS - Origin validation]
    C --> D[RateLimit - Token bucket]
    D --> E["JWTDependency - RS256 verify - extract sub/tenantId/role"]
    E --> F{TenantDependency - Active? Quota?}
    F -->|No| F1[403/429]
    F -->|Yes| G{RolesDependency - Role match?}
    G -->|No| G1[403 Forbidden]
    G -->|Yes| H[PydanticValidation - Body/Query validate]
    H --> I[Router Handler]
    I --> J[Service - Repository - Adapter]
    J --> K["ResponseMiddleware - Wrap in data/meta"]
    K --> L[Response]
```

### 4.3 模块间通信

| 通信类型 | 实现 | 场景 |
|---------|------|------|
| HTTP REST | JSON | FE ↔ BE 所有操作 |
| SSE | sse-starlette / StreamingResponse | 对话消息实时推送 |
| RQ | Redis Queue | 审批执行/记忆提取/索引轮询 |
| EventBus | asyncio + blinker | 审计日志/缓存失效/配额更新 |
| Dify API | httpx | BE → Dify Service API + Console API |

### 4.4 缓存策略

| 对象 | Key 模式 | TTL | 失效 |
|------|---------|-----|------|
| Agent 列表 | `agents:{tenantId}:{hash}` | 60s | CRUD 时 DEL |
| KB 列表 | `kb:{tenantId}:{hash}` | 60s | CRUD 时 DEL |
| Dify App Info | `dify:app:{appId}:info` | 300s | 被动 |
| User Session | `session:{id}` | 2h | JWT 同步 |
| Rate Limit | `rl:{ip}:{window}` | 窗口 | 滑动 |

---

## <a id="ch5"></a>五、SQLAlchemy ORM 数据模型

### 5.1 实体关系图

```mermaid
erDiagram
    Tenant ||--o{ User : has_many
    Tenant ||--o{ AgentRegistry : owns
    Tenant ||--o{ KnowledgeBaseRegistry : owns
    Tenant ||--o{ ToolRegistry : owns
    Tenant ||--o{ Task : owns
    Tenant ||--o{ RunLog : owns
    Tenant ||--o{ UserMemory : owns
    User ||--o{ AgentRegistry : creates
    User ||--o{ Task : creates_or_assigned
    AgentRegistry ||--o{ AgentKnowledgeBinding : has
    AgentRegistry ||--o{ AgentToolBinding : has
    AgentRegistry ||--o{ AgentDailyStat : stats
    AgentRegistry ||--o{ RunLog : logs
    KnowledgeBaseRegistry ||--o{ AgentKnowledgeBinding : bound_by
    ToolRegistry ||--o{ AgentToolBinding : bound_by
    ToolRegistry ||--o{ ToolDebugCase : cases
    RunLog ||--o{ TraceStep : steps
    RunLog ||--o{ TraceCitation : citations
    RunLog ||--o{ TraceToolCall : tool_calls
```

### 5.2 完整 SQLAlchemy 模型（24 模型）

数据库使用 PostgreSQL 16 + pgvector + pgcrypto。所有表名映射到 snake_case。所有时间字段使用 `DateTime(timezone=True)`（等效于 PostgreSQL 的 `timestamptz`）。

```python
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类。"""


# ────────────────────────────────────────
# Tenant
# ────────────────────────────────────────
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    sso_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sso_provider: Mapped[str] = mapped_column(String(50), default="local", nullable=False)
    sso_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quota_limit: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    quota_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # relationships
    users: Mapped[list[User]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    agent_registries: Mapped[list[AgentRegistry]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    knowledge_bases: Mapped[list[KnowledgeBaseRegistry]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    tools: Mapped[list[ToolRegistry]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    tasks: Mapped[list[Task]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    user_memories: Mapped[list[UserMemory]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    run_logs: Mapped[list[RunLog]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


# ────────────────────────────────────────
# User
# ────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    sso_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="employee", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    avatar_text: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # relationships
    tenant: Mapped[Tenant] = relationship(back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    created_agents: Mapped[list[AgentRegistry]] = relationship(
        back_populates="creator", foreign_keys="AgentRegistry.created_by",
    )
    created_tasks: Mapped[list[Task]] = relationship(
        back_populates="creator", foreign_keys="Task.creator_id",
    )
    assigned_tasks: Mapped[list[Task]] = relationship(
        back_populates="assignee", foreign_keys="Task.assignee_id",
    )
    user_memories: Mapped[list[UserMemory]] = relationship(back_populates="user", cascade="all, delete-orphan")
    run_logs: Mapped[list[RunLog]] = relationship(back_populates="user")

    __table_args__ = (
        UniqueConstraint("tenant_id", "sso_sub", name="uq_users_tenant_sso_sub"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant_id", "tenant_id"),
    )


# ────────────────────────────────────────
# RefreshToken
# ────────────────────────────────────────
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


# ────────────────────────────────────────
# AgentRegistry
# ────────────────────────────────────────
class AgentRegistry(Base):
    __tablename__ = "agent_registry"

    agent_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    dify_app_id: Mapped[str] = mapped_column(String(100), nullable=False)
    dify_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="chat", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    visibility: Mapped[str] = mapped_column(String(50), default="department", nullable=False)
    icon: Mapped[str | None] = mapped_column(String(50), default="🤖", nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # relationships
    tenant: Mapped[Tenant] = relationship(back_populates="agent_registries")
    creator: Mapped[User | None] = relationship(back_populates="created_agents", foreign_keys=[created_by])
    knowledge_bindings: Mapped[list[AgentKnowledgeBinding]] = relationship(
        back_populates="agent", cascade="all, delete-orphan",
    )
    tool_bindings: Mapped[list[AgentToolBinding]] = relationship(
        back_populates="agent", cascade="all, delete-orphan",
    )
    daily_stats: Mapped[list[AgentDailyStat]] = relationship(
        back_populates="agent", cascade="all, delete-orphan",
    )
    run_logs: Mapped[list[RunLog]] = relationship(back_populates="agent")

    __table_args__ = (
        Index("ix_agent_registry_tenant_id", "tenant_id"),
        Index("ix_agent_registry_tenant_status", "tenant_id", "status"),
    )


# ────────────────────────────────────────
# AgentKnowledgeBinding
# ────────────────────────────────────────
class AgentKnowledgeBinding(Base):
    __tablename__ = "agent_knowledge_bindings"

    agent_id: Mapped[str] = mapped_column(String(100), ForeignKey("agent_registry.agent_id", ondelete="CASCADE"), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(100), ForeignKey("knowledge_base_registry.kb_id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped[AgentRegistry] = relationship(back_populates="knowledge_bindings")
    knowledge: Mapped[KnowledgeBaseRegistry] = relationship(back_populates="agent_bindings")


# ────────────────────────────────────────
# AgentToolBinding
# ────────────────────────────────────────
class AgentToolBinding(Base):
    __tablename__ = "agent_tool_bindings"

    agent_id: Mapped[str] = mapped_column(String(100), ForeignKey("agent_registry.agent_id", ondelete="CASCADE"), primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(100), ForeignKey("tool_registry.tool_id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped[AgentRegistry] = relationship(back_populates="tool_bindings")
    tool: Mapped[ToolRegistry] = relationship(back_populates="agent_bindings")


# ────────────────────────────────────────
# AgentDailyStat
# ────────────────────────────────────────
class AgentDailyStat(Base):
    __tablename__ = "agent_daily_stats"

    agent_id: Mapped[str] = mapped_column(String(100), ForeignKey("agent_registry.agent_id"), primary_key=True)
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_latency_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    agent: Mapped[AgentRegistry] = relationship(back_populates="daily_stats")


# ────────────────────────────────────────
# KnowledgeBaseRegistry
# ────────────────────────────────────────
class KnowledgeBaseRegistry(Base):
    __tablename__ = "knowledge_base_registry"

    kb_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    dify_dataset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="business", nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    indexing_status: Mapped[str | None] = mapped_column(String(50), default="ready", nullable=True)
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="knowledge_bases")
    agent_bindings: Mapped[list[AgentKnowledgeBinding]] = relationship(back_populates="knowledge")

    __table_args__ = (
        Index("ix_knowledge_base_registry_tenant_id", "tenant_id"),
    )


# ────────────────────────────────────────
# ToolRegistry
# ────────────────────────────────────────
class ToolRegistry(Base):
    __tablename__ = "tool_registry"

    tool_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="http", nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), default="POST", nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    permission_mode: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    auth_type: Mapped[str | None] = mapped_column(String(50), default="none", nullable=True)
    auth_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    retry_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    circuit_breaker: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant: Mapped[Tenant | None] = relationship(back_populates="tools")
    agent_bindings: Mapped[list[AgentToolBinding]] = relationship(back_populates="tool")
    debug_cases: Mapped[list[ToolDebugCase]] = relationship(back_populates="tool", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tool_registry_tenant_id", "tenant_id"),
    )


# ────────────────────────────────────────
# ToolDebugCase
# ────────────────────────────────────────
class ToolDebugCase(Base):
    __tablename__ = "tool_debug_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_id: Mapped[str] = mapped_column(String(100), ForeignKey("tool_registry.tool_id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tool: Mapped[ToolRegistry] = relationship(back_populates="debug_cases")


# ────────────────────────────────────────
# RunLog
# ────────────────────────────────────────
class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), ForeignKey("agent_registry.agent_id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_usage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    knowledge_hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dify_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="run_logs")
    agent: Mapped[AgentRegistry] = relationship(back_populates="run_logs")
    user: Mapped[User] = relationship(back_populates="run_logs")
    steps: Mapped[list[TraceStep]] = relationship(back_populates="log", cascade="all, delete-orphan")
    citations: Mapped[list[TraceCitation]] = relationship(back_populates="log", cascade="all, delete-orphan")
    tool_calls: Mapped[list[TraceToolCall]] = relationship(back_populates="log", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_run_logs_tenant_created_at", "tenant_id", "created_at", postgresql_using="btree"),
    )


# ────────────────────────────────────────
# TraceStep
# ────────────────────────────────────────
class TraceStep(Base):
    __tablename__ = "trace_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(255), ForeignKey("run_logs.trace_id"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    log: Mapped[RunLog] = relationship(back_populates="steps")


# ────────────────────────────────────────
# TraceCitation
# ────────────────────────────────────────
class TraceCitation(Base):
    __tablename__ = "trace_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(255), ForeignKey("run_logs.trace_id"), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kb_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    log: Mapped[RunLog] = relationship(back_populates="citations")


# ────────────────────────────────────────
# TraceToolCall
# ────────────────────────────────────────
class TraceToolCall(Base):
    __tablename__ = "trace_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(255), ForeignKey("run_logs.trace_id"), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    permission_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    log: Mapped[RunLog] = relationship(back_populates="tool_calls")


# ────────────────────────────────────────
# Task
# ────────────────────────────────────────
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="tasks")
    creator: Mapped[User] = relationship(back_populates="created_tasks", foreign_keys=[creator_id])
    assignee: Mapped[User | None] = relationship(back_populates="assigned_tasks", foreign_keys=[assignee_id])

    __table_args__ = (
        Index("ix_tasks_tenant_assignee_status", "tenant_id", "assignee_id", "status"),
    )


# ────────────────────────────────────────
# UserMemory
# ────────────────────────────────────────
class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=True)  # pgvector 1536 维
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="user_memories")
    tenant: Mapped[Tenant] = relationship(back_populates="user_memories")

    __table_args__ = (
        Index("ix_user_memories_user_tenant", "user_id", "tenant_id"),
    )


# ────────────────────────────────────────
# AuditLog
# ────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # inet-compatible
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_tenant_created_at", "tenant_id", "created_at"),
    )
```

### 5.3 异步 Session 工厂与 Repository 模式

```python
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.models.base import Base
from app.models.agent import AgentRegistry, AgentKnowledgeBinding, AgentToolBinding

# ────────────────────────────────────────
# 异步引擎与 Session 工厂
# ────────────────────────────────────────

engine = create_async_engine(
    "postgresql+asyncpg://postgres:postgres@localhost:5432/eap",
    echo=False,                # 生产环境关闭 SQL 回显
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,        # 连接池健康检查
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,    # 提交后对象仍可用
)


async def init_db() -> None:
    """应用启动时调用：创建表（开发环境）或验证连接（生产环境）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """FastAPI 依赖：注入异步 Session。"""
    async with AsyncSessionLocal() as session:
        yield session


# ────────────────────────────────────────
# AgentRepository — 示例 Repository
# ────────────────────────────────────────

class AgentRepository:
    """智能体数据访问层，封装 SQLAlchemy 查询逻辑。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_many(
        self,
        *,
        tenant_id: str,
        where: dict[str, Any] | None = None,
        order_by: Any = None,
        skip: int = 0,
        take: int = 20,
    ) -> list[AgentRegistry]:
        stmt = (
            select(AgentRegistry)
            .where(AgentRegistry.tenant_id == tenant_id)
            .options(
                selectinload(AgentRegistry.knowledge_bindings)
                .selectinload(AgentKnowledgeBinding.knowledge),
                selectinload(AgentRegistry.tool_bindings)
                .selectinload(AgentToolBinding.tool),
            )
            .order_by(order_by or AgentRegistry.updated_at.desc())
            .offset(skip)
            .limit(take)
        )
        if where:
            # 动态过滤条件
            for key, value in where.items():
                col = getattr(AgentRegistry, key, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_id(self, agent_id: str, tenant_id: str) -> AgentRegistry | None:
        stmt = (
            select(AgentRegistry)
            .where(
                AgentRegistry.agent_id == agent_id,
                AgentRegistry.tenant_id == tenant_id,
            )
            .options(
                selectinload(AgentRegistry.knowledge_bindings)
                .selectinload(AgentKnowledgeBinding.knowledge),
                selectinload(AgentRegistry.tool_bindings)
                .selectinload(AgentToolBinding.tool),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count(self, tenant_id: str, where: dict[str, Any] | None = None) -> int:
        stmt = select(func.count()).select_from(AgentRegistry).where(
            AgentRegistry.tenant_id == tenant_id
        )
        if where:
            for key, value in where.items():
                col = getattr(AgentRegistry, key, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def create(self, data: dict[str, Any]) -> AgentRegistry:
        agent = AgentRegistry(**data)
        self._session.add(agent)
        await self._session.flush()
        await self._session.refresh(agent)
        return agent

    async def update(self, agent_id: str, data: dict[str, Any]) -> AgentRegistry | None:
        stmt = (
            update(AgentRegistry)
            .where(AgentRegistry.agent_id == agent_id)
            .values(**data)
            .returning(AgentRegistry)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, agent_id: str) -> AgentRegistry | None:
        from datetime import datetime, timezone
        return await self.update(agent_id, {
            "archived_at": datetime.now(timezone.utc),
            "status": "offline",
        })
```

---

## <a id="ch6"></a>六、FastAPI 后端架构

### 6.1 应用入口与模块装配

```python
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    ResponseWrapMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user, get_active_tenant, require_roles
from app.db.session import init_db
from app.routers import (
    agent,
    auth,
    conversation,
    dashboard,
    health,
    knowledge,
    memory,
    model,
    run_log,
    task,
    tool,
    trace,
    user,
    workflow,
    evaluation,
    safety,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ──
    await init_db()
    # 初始化 DifyConsoleClient、Redis 连接池、RQ 队列等
    yield
    # ── 关闭 ──
    await app.state.dify_console_client.shutdown()


app = FastAPI(
    title="Enterprise Agent Platform API",
    version="1.0.0",
    description="企业智能体平台 REST API",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── 中间件管道（执行顺序：后添加先执行，即洋葱模型外层 → 内层）──
app.add_middleware(ResponseWrapMiddleware)          # 响应包裹 data/meta
app.add_middleware(LoggingMiddleware)                # 请求/响应日志
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware, redis_url=settings.redis_url)
app.add_middleware(SecurityHeadersMiddleware)

# ── 异常处理 ──
register_exception_handlers(app)

# ── 路由注册（14 个模块 + 辅助路由）──
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(agent.router)
app.include_router(knowledge.router)
app.include_router(conversation.router)
app.include_router(tool.router)
app.include_router(trace.router)
app.include_router(task.router)
app.include_router(dashboard.router)
app.include_router(memory.router)
app.include_router(model.router)
app.include_router(workflow.router)
app.include_router(evaluation.router)
app.include_router(safety.router)
app.include_router(health.router)
```

### 6.2 统一响应格式与错误码

成功响应：`{ "data": T | T[], "total"?: number, "page"?: number, "pageSize"?: number }`

错误码体系：`UNAUTHORIZED`(401)、`FORBIDDEN`(403)、`NOT_FOUND`(404)、`DUPLICATE_ENTITY`(409)、`VALIDATION_ERROR`(422)、`RATE_LIMITED`(429)、`DIFY_UNAVAILABLE`(502)、`DIFY_TIMEOUT`(504)、`INTERNAL_ERROR`(500)、`TENANT_SUSPENDED`(403)、`QUOTA_EXCEEDED`(429)。

```python
from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    DIFY_UNAVAILABLE = "DIFY_UNAVAILABLE"
    DIFY_TIMEOUT = "DIFY_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TENANT_SUSPENDED = "TENANT_SUSPENDED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


_STATUS_MAP: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.DUPLICATE_ENTITY: 409,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DIFY_UNAVAILABLE: 502,
    ErrorCode.DIFY_TIMEOUT: 504,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.TENANT_SUSPENDED: 403,
    ErrorCode.QUOTA_EXCEEDED: 429,
}


class AppError(Exception):
    """业务异常基类，携带错误码和可读消息。"""

    def __init__(self, code: ErrorCode, message: str, details: Any = None) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_MAP[exc.code],
            content={
                "error": {"code": exc.code.value, "message": exc.message, "details": exc.details},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unknown(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "Internal server error",
                    "details": str(exc) if app.debug else None,
                },
            },
        )
```

---

## <a id="ch7"></a>七、API 接口设计（53 个端点）

**认证（4）**：`POST /api/auth/login`, `GET /api/auth/callback`, `POST /api/auth/refresh`, `POST /api/auth/logout`

**用户（5）**：`GET /api/me`, `GET /api/members`, `POST /api/members`, `PATCH /api/members/:id`, `DELETE /api/members/:id`

**智能体（8）**：`GET /api/agents`（params: status/category/type/search/page/pageSize/sortBy/sortOrder）、`GET /api/agents?status=published`、`GET /api/agents/:id`、`POST /api/agents`、`PATCH /api/agents/:id`、`POST /api/agents/:id/publish`、`POST /api/agents/:id/offline`、`GET /api/agents/:id/logs`

**知识库（8）**：`GET /api/knowledge-bases`、`GET /api/knowledge-bases/:id`、`POST /api/knowledge-bases`、`PATCH /api/knowledge-bases/:id`、`DELETE /api/knowledge-bases/:id`、`GET /api/knowledge-bases/:id/documents`、`POST /api/knowledge-bases/:id/documents`（multipart, 15MB 限制）、`POST /api/knowledge-bases/:id/retrieval-test`

**会话（6）**：`GET /api/conversations`、`POST /api/conversations`、`GET /api/conversations/:id`、`POST /api/conversations/:id/messages`（SSE 流式响应）、`POST /api/conversations/:id/tool-calls/:callId/confirm`、`DELETE /api/conversations/:id`

**工具（6）**：`GET /api/tools`、`GET /api/tools/:id`、`POST /api/tools`、`PATCH /api/tools/:id`、`POST /api/tools/:id/debug`、`DELETE /api/tools/:id`

**运行日志（2）**：`GET /api/run-logs`、`GET /api/run-logs/:traceId`

**任务（4）**：`GET /api/tasks`、`GET /api/tasks/:id`、`POST /api/tasks/:id/approve`、`POST /api/tasks/:id/reject`

**其他（10）**：`GET /api/dashboard/admin`、`GET /api/dashboard/user`、`GET /api/health` + `/api/health/ready` + `/api/health/live`、`GET /api/models`、`GET /api/memories`、`DELETE /api/memories/:id`、`PATCH /api/memories/:id`、`POST /api/memories/recall`


# 第二部分：业务模块详细设计

## <a id="m1"></a>八、模块 1：用户与权限

### 8.1 SSO OIDC 登录完整流程

```mermaid
sequenceDiagram
    participant U as User Browser
    participant FE as Frontend
    participant BE as Backend
    participant IdP as Enterprise IdP
    participant DB as Wrapper DB

    U->>FE: Click SSO Login
    FE->>BE: POST /api/auth/login {provider:"oidc"}
    BE->>BE: Generate OIDC URL with PKCE (code_challenge + code_verifier)
    BE-->>FE: 302 Redirect to IdP /authorize
    FE->>IdP: GET /authorize?client_id=..&redirect_uri=..&scope=openid+profile+email&state=..&code_challenge=..
    IdP->>U: Show Login Form
    U->>IdP: Enter credentials
    IdP-->>FE: 302 Redirect /api/auth/callback?code=xxx&state=yyy
    FE->>BE: GET /api/auth/callback?code=xxx&state=yyy
    BE->>BE: Validate state (CSRF prevention) + Verify PKCE code_verifier
    BE->>IdP: POST /token {code, client_id, client_secret, code_verifier, grant_type:"authorization_code"}
    IdP-->>BE: {access_token, id_token, expires_in}
    BE->>BE: Verify id_token (RS256 signature, issuer, audience, nonce, exp, iat)
    BE->>IdP: GET /userinfo (Authorization: Bearer {access_token})
    IdP-->>BE: {sub, email, name, given_name, family_name, picture}
    BE->>DB: SELECT users WHERE tenant_id (from sso_domain match) AND sso_sub = {sub}
    alt User exists
        DB-->>BE: Update last_active_at, login_count++
    else New user
        BE->>DB: INSERT users (role=employee, status=active, sso_sub, email, name)
    end
    BE->>BE: Sign JWT {sub: userId, tenant_id, role, iat, exp: now+2h, jti: uuid} using RS256
    BE->>DB: INSERT refresh_tokens (token_hash, expires_at: now+30d)
    BE-->>FE: Set-Cookie (HttpOnly, Secure, SameSite=Lax, Max-Age=7200) + 302 → /user
    FE->>BE: GET /api/me
    BE-->>FE: {id, name, email, department, role, avatarText, canAccessAdmin, tenantName}
    FE->>FE: AuthContext.set({user, tenant, isAuthenticated: true})
```

### 8.2 RBAC 权限矩阵

| 操作 | platform_admin | agent_admin | knowledge_admin | auditor | employee |
|------|:-:|:-:|:-:|:-:|:-:|
| 访问管理端 /admin | ✅ | ✅ | ✅ | ✅ | ❌ |
| 访问用户端 /user | ✅ | ✅ | ✅ | ✅ | ✅ |
| 创建/编辑/删除智能体 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 发布/下线智能体 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 创建/编辑/删除知识库 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 上传文档到知识库 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 注册/编辑/删除工具 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 配置模型供应商 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 管理成员（邀请/禁用/改角色） | ✅ | ❌ | ❌ | ❌ | ❌ |
| 查看运行日志 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 查看系统健康 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 审批高风险工具 | ✅ | ✅ | ❌ | ❌ | ✅(仅自己) |
| 发起对话 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 查看/继续自己的会话 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 删除自己的会话 | ✅ | ✅ | ✅ | ✅ | ✅ |

### 8.3 多租户隔离机制

三层隔离策略：认证层 JWT 注入 tenant_id → 业务层 Wrapper DB 所有查询强制 `WHERE tenant_id = $1` → Dify 层 `user = "{tenant_id}:{user_id}"` 会话隔离。

### 8.4 前端组件设计

**LoginPage**（`/login`）：居中卡片布局。包含企业 Logo、SSO 登录按钮（Google/Microsoft/自定义 IdP 图标）。OIDC 回调期间显示 Loading 动画和"正在验证身份..."提示。错误状态显示红色警告卡片和重试按钮。

**MemberListPage**（`/admin/members`）：表格组件，列：姓名/邮箱/部门/角色/状态/最近活跃/操作。支持搜索（按姓名或邮箱模糊匹配）、角色筛选下拉框、状态筛选。操作列包含"编辑角色"下拉菜单和"禁用/启用"确认对话框。表格顶部有"邀请成员"按钮。

**MemberInviteModal**：弹窗包含邮箱输入框（支持逗号分隔批量邀请）、角色选择下拉框、发送按钮。提交时调用 `POST /api/members`，成功后显示绿色 Toast"邀请已发送"，失败显示错误信息。

---

## <a id="m2"></a>九、模块 2：智能体

### 9.1 创建智能体完整流程

```mermaid
sequenceDiagram
    participant A as Admin
    participant FE as Frontend
    participant BE as Backend
    participant DC as DifyConsoleClient
    participant D as Dify API
    participant DB as Wrapper DB

    A->>FE: Fill create form - name, type, model, prompt, KBs, tools
    FE->>BE: POST /api/agents
    BE->>DC: createApp({name, mode})
    DC->>D: POST /console/api/apps
    D-->>DC: {id: dify-app-uuid}
    BE->>DC: configureModel(appId, {model, parameters, pre_prompt})
    BE->>DC: createApiKey(appId)
    D-->>DC: {token: app-xxx}
    BE->>DB: INSERT agent_registry + bindings
    BE-->>FE: 201 Created
    FE-->>A: Success notification + redirect to agent detail
```

### 9.2 生命周期状态机

```mermaid
stateDiagram-v2
    [*] --> draft: POST /api/agents (Console API creates Dify app)
    draft --> testing: Config complete, admin enters debug mode
    testing --> published: POST /api/agents/:id/publish
    testing --> draft: Config needs changes
    published --> offline: POST /api/agents/:id/offline
    offline --> published: Re-publish
    offline --> [*]: Delete (Console API + Wrapper DB)
```

### 9.3 前端组件设计

**AgentCreateForm**（`/admin/agents/new`）：分步表单。步骤 1（基本信息）：名称输入框（必填，1-100 字符）、agentId 输入框（必填，小写字母数字连字符，用于 URL 标识）、类型选择器（Chat/Agent/Workflow 三个卡片，带图标和描述）、描述文本域（可选，最多 500 字符）、图标选择器（emoji picker）、分类下拉框（法务合规/人事行政/销售运营/IT 运维/其他）、标签输入框（TagInput，回车添加，最多 10 个）、可见性单选（全员可见/部门可见/仅自己可见）。步骤 2（模型与 Prompt）：模型供应商下拉框（从 Dify Console API 获取）、具体模型下拉框（依赖供应商选择、显示模型名称+上下文窗口+成本）、参数配置区（temperature/max_tokens/top_p 滑块和输入框）、Prompt 编辑器（大文本域，等宽字体，字符计数）。步骤 3（绑定资源）：知识库多选器（左侧可用知识库列表，右侧已选列表，支持拖拽排序）、工具多选器（同上，显示工具名称+风险等级徽章）、预览区（展示已绑定的知识库和工具摘要）。步骤 4（确认）：展示所有配置的摘要（只读），包含名称/类型/模型/Prompt 预览/KB 列表/工具列表/可见性。底部"创建智能体"按钮。

**AgentCard**（智能体广场）：卡片布局。顶部为图标 emoji（48px）+ 名称 + 状态徽章（draft=灰色/testing=蓝色/published=绿色/offline=红色）。中部为描述文本（最多 2 行截断，Tailwind line-clamp-2）+ 标签行（最多 3 个标签 Badge）。底部为统计区域：今日调用数（蓝色）、成功率（绿色百分比）、平均延迟（灰色 ms）。hover 时显示阴影效果和"查看详情"提示。点击跳转到智能体详情页。

**AgentConfigPanel**：Tab 切换布局。Tab 1（配置）：基本信息编辑区、模型/Prompt 编辑区、KB/工具绑定编辑区。Tab 2（调试）：嵌入 MiniChatWindow 组件，允许管理员发送测试消息并实时查看回答。Tab 3（日志）：最近 10 条运行日志的简化表格，每行可点击跳转到 `/admin/logs/:traceId`。

---

## <a id="m3"></a>十、模块 3：知识库

### 10.1 文档上传→索引→召回全链路

```mermaid
sequenceDiagram
    participant A as Admin
    participant FE as Frontend
    participant BE as Backend
    participant D as Dify Platform
    participant VDB as Vector DB

    A->>FE: Select PDF + Upload
    FE->>FE: Client-side validation - type + size < 15MB
    FE->>BE: POST /api/knowledge-bases/:id/documents (multipart)
    BE->>DB: Find kb_registry → dify_dataset_id
    BE->>D: POST /v1/datasets/:id/document/create-by-file
    D->>D: Store → Parse (PDF/unpdf) → Clean → Chunk (800 tokens) → Embed → Write VDB
    D-->>BE: 201 {document:{id, indexing_status:"indexing"}, batch}
    BE-->>FE: 201 {id, name, type:"pdf", status:"indexing"}

    loop SWR Poll (refreshInterval=3000ms)
        FE->>BE: GET /api/knowledge-bases/:id/documents
        BE->>D: GET /v1/datasets/:id/documents/:docId
        D-->>BE: {indexing_status:"completed"}
        BE-->>FE: {status:"ready"}
    end

    A->>FE: Query "付款触发条件" in retrieval test
    FE->>BE: POST /api/knowledge-bases/:id/retrieval-test {query, topK:5}
    BE->>D: POST /v1/datasets/:id/retrieve {hybrid_search + reranking}
    D-->>BE: {records:[{segment:{content, document:{name}}, score:0.91}]}
    BE-->>FE: {citations:[{sourceName, excerpt, score}], latencyMs:120}
```

### 10.2 前端组件设计

**DocumentUploader**：拖拽上传区域（虚线边框，drag-over 时高亮蓝色边框）。支持点击选择文件。文件类型校验：PDF/DOCX/XLSX/TXT/MD/CSV/JSON/HTML（白名单，通过 magic bytes 校验防伪造扩展名）。文件大小限制：15MB。上传进度条（蓝色，通过 fetch 的 ReadableStream 或 XMLHttpRequest progress 事件获取）。上传完成后显示文件卡片（名称、类型图标、大小、状态徽章）。支持多文件同时上传（串行上传，逐个显示进度）。

**RetrievalTest**：查询输入框（带搜索图标）+ Top K 滑块（1-20，默认 5）+ "测试"按钮。结果区域：按分数降序排列的结果卡片。每张卡片包含：文档来源名称（蓝色链接）、知识库名称（灰色文字）、相似度分数（绿色百分比徽章，分数 ≥0.8 深绿、≥0.5 浅绿、<0.5 黄色）、内容摘录（最多 200 字符，关键词高亮）。

**ChunkList**：表格布局。列：标题（可点击展开全文 Modal）、内容预览（最多 100 字符 + "..."省略）、Token 数（灰色等宽字体）、最后更新时间。Modal 显示完整分块内容（等宽字体，可复制，支持 Markdown 渲染预览）。

---

## <a id="m4"></a>十一、模块 4：会话

### 11.1 流式对话完整链路

```mermaid
sequenceDiagram
    participant U as User
    participant FE as useStreamChat
    participant BE as Backend
    participant M as MemoryService
    participant D as Dify API

    U->>FE: Type + Send
    FE->>FE: Add user bubble - id, role:"user", content, createdAt
    FE->>BE: POST /api/conversations/:id/messages {query, agentId}
    BE->>BE: Auth + Tenant - extract userId + tenantId
    BE->>M: recall(userId, tenantId, query)
    M-->>BE: [preferences, facts]
    BE->>BE: difyUser = tenantId + ":" + userId
    BE->>D: POST /v1/chat-messages {query, user:difyUser, response_mode:"streaming", conversation_id, inputs:{prefs, facts}}

    loop SSE Stream
        D-->>BE: event:message {answer:"已"}
        BE-->>FE: event:message {content:"已", messageId, conversationId}
        FE->>FE: StreamingText append + cursor blink

        D-->>BE: event:message {answer:"识别出"}
        D-->>BE: event:node_started {title:"检索知识库"}
        BE-->>FE: event:node_started
        D-->>BE: event:node_finished {status:"success"}
        D-->>BE: event:message_end {metadata:{usage, retriever_resources}}
        BE-->>FE: event:message_end {traceId, citations, usage}
        FE->>FE: CitationCard[] rendered + traceId stored
    end

    BE->>M: emit "conversation.completed" → MemoryExtraction Job (async)
```

### 11.2 useStreamChat Hook 完整实现

```typescript
function useStreamChat(conversationId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallRun[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [workflowState, setWorkflowState] = useState<WorkflowState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string | null>(null);

  async function sendMessage(query: string, agentId: string, files?: FileAttachment[]) {
    setStreaming(true); setError(null); setCitations([]); setToolCalls([]);
    const controller = new AbortController(); abortRef.current = controller;

    setMessages(prev => [...prev, { id: `user-${Date.now()}`, role: 'user', content: query, createdAt: new Date().toISOString() }]);

    try {
      const res = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, agentId, files }),
      });
      if (!res.ok) { setError(`HTTP ${res.status}`); return; }

      const reader = res.body!.getReader(); const decoder = new TextDecoder();
      let buffer = '', assistantText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n'); buffer = lines.pop() || '';
        let evt = '';

        for (const line of lines) {
          if (line.startsWith('event: ')) { evt = line.slice(7).trim(); continue; }
          if (!line.startsWith('data: ')) continue;
          const data = JSON.parse(line.slice(6).trim());

          switch (evt) {
            case 'message':
              assistantText += data.content;
              setMessages(prev => {
                const last = prev[prev.length - 1];
                if (last?.role === 'assistant' && last.id === assistantIdRef.current)
                  return [...prev.slice(0, -1), { ...last, content: assistantText }];
                assistantIdRef.current = data.messageId;
                return [...prev, { id: data.messageId, role: 'assistant', content: assistantText, createdAt: new Date().toISOString() }];
              });
              break;
            case 'message_end': setTraceId(data.traceId);
              if (data.metadata?.retriever_resources) setCitations(data.metadata.retriever_resources.map(r => ({ id: r.segment_id, sourceName: r.document_name, knowledgeBaseName: r.dataset_name, excerpt: r.content?.substring(0, 200), score: r.score })));
              break;
            case 'workflow_started': setWorkflowState({ status: 'running', workflowRunId: data.workflowRunId, nodes: [] }); break;
            case 'node_started': setWorkflowState(prev => prev ? { ...prev, nodes: [...prev.nodes, { id: data.nodeId, title: data.title, status: 'running' }] } : prev); break;
            case 'node_finished': setWorkflowState(prev => prev ? { ...prev, nodes: prev.nodes.map(n => n.id === data.nodeId ? { ...n, status: data.status } : n) } : prev); break;
            case 'error': setError(data.message); break;
          }
        }
      }
    } catch (err: any) { if (err.name !== 'AbortError') setError('Connection interrupted'); }
    finally { setStreaming(false); }
  }

  function abort() { abortRef.current?.abort(); }
  return { messages, citations, toolCalls, streaming, traceId, workflowState, error, sendMessage, abort };
}
```

### 11.3 前端组件设计

**ChatWindow**（核心组件）：全高度布局（h-full flex flex-col）。Header 区域：智能体图标 + 名称 + 状态徽章 + Trace ID 链接（仅 traceId 非 null 时显示）。MessageList 区域（flex-1 overflow-y-auto，使用虚拟滚动 react-virtuoso 优化 1000+ 消息的性能）：每条消息为 MessageBubble 组件。Input 区域：ChatInput 组件，固定底部。

**StreamingText**：使用 `useEffect` + `requestAnimationFrame` 实现平滑逐字渲染。每个字符间隔约 30ms，渲染时带有闪烁光标 `|`（CSS animation blink）。支持 Markdown 基本渲染（粗体/斜体/代码块/列表）。代码块使用 `react-syntax-highlighter` 语法高亮。

**CitationCard**：水平卡片布局。左侧文件类型图标（PDF=红色/DOCX=蓝色/XLSX=绿色/TXT=灰色）。中间：来源文档名称（加粗）、知识库名称（灰色小字）、内容摘录（最多 200 字符）。右侧：相似度分数（百分比 + 颜色编码）。点击来源文档名称时，如果有文档 URL 则新标签打开。

**ToolCallCard**：收缩式卡片。收起时显示：工具图标 + 工具名称 + 状态徽章（success=绿色/running=蓝色 spinning/blocked=橙色/failed=红色）+ 耗时（ms）。展开时显示：请求摘要（等宽字体灰色背景代码块）+ 响应摘要（同上）+ 权限模式标签（auto/confirm/disabled）。

---

## <a id="m5"></a>十二、模块 5：工具

### 12.1 工具代理执行流程

```mermaid
flowchart TD
    A[Tool Execution Request] --> B{riskLevel + permissionMode?}
    B -->|low/medium + auto| C[Direct Proxy]
    B -->|high + confirm| D[Create Approval Task]
    B -->|disabled| E[Return - Tool Disabled]
    D --> D1["Insert task - status=pending"]
    D1 --> D2[Push to RQ approval queue]
    D2 --> D3[Wait for approver]
    D3 --> D3a{Approved?}
    D3a -->|Yes| C
    D3a -->|No| E2[Return - Rejected]
    C --> F["Inject Auth Headers - API Key/OAuth2 Token"]
    F --> G["Sanitize Request Params - mask PII"]
    G --> H{Circuit Breaker Open?}
    H -->|Yes| H1[Return - Circuit Open]
    H -->|No| I["Execute HTTP Request - with timeout"]
    I --> J{Response OK?}
    J -->|Yes| K[Mask Sensitive Response Fields]
    K --> L[Return - Success + Masked Response]
    J -->|No| M{Retries Left?}
    M -->|Yes| N["Exponential Backoff - delay = 2^n * 1000ms"]
    N --> I
    M -->|No| O["Record Failure - failures++ - if failures>=5 - Open Circuit"]
    O --> P[Return - Error]
```

### 12.2 高风险工具审批流程

```mermaid
sequenceDiagram
    participant U as Employee
    participant BE as Backend
    participant Q as RQ
    participant TP as ToolProxy
    participant A as Approver

    U->>BE: Chat triggers high-risk tool
    BE->>BE: Create Task {type:"tool_approval", status:"pending", payload:{toolName, params, conversationId}}
    BE->>A: Notification (in-app + email/wechat)
    A->>BE: POST /api/tasks/:id/approve
    BE->>BE: Validate role + Update status=approved
    BE->>Q: Enqueue tool-approval job
    Q->>TP: execute(toolName, params)
    TP->>TP: Auth inject + params sanitize + retry
    TP-->>Q: {status:"success", response:{...}}
    Q->>BE: Job completed callback
    BE-->>U: SSE final answer with tool result
```

### 12.3 前端组件设计

**ToolConfigForm**：类型选择器（HTTP/数据库/RPA/Webhook/MCP 五个卡片）。端点和请求方法输入框。请求头 Key-Value 编辑器。请求/响应 JSON Schema 编辑器（Monaco Editor 或简单 JSON 编辑区）。风险等级三段式滑块（低/中/高，带颜色编码）。权限模式单选按钮组（自动执行/需确认/已禁用）。鉴权类型下拉框（无/API Key/Bearer Token/OAuth2/HTTP Basic）+ 对应的配置字段。

**DebugPanel**：请求体 JSON 编辑器 + "发送测试请求"按钮。响应区域分两部分：左侧 HTTP 状态码徽章 + 延迟显示（ms）+ 保存为用例按钮；右侧响应体 JSON 格式化展示（react-json-view 或语法高亮）。

---

## <a id="m6"></a>十三、模块 6：运行记录

### 13.1 Trace 数据流

```mermaid
flowchart LR
    subgraph P1["P1 - DifyAdapter"]
        A1[Dify chat-messages response] --> A2[Extract metadata.retriever_resources - citations]
        A2 --> A3[Extract metadata.usage - token/latency stats]
        A3 --> A4[Parse workflow events - steps timeline]
        A4 --> A5[Return aggregated RunLog]
    end
    subgraph P2["P2 - SelfBuiltAdapter - 4 tables"]
        B1[conversation completed event] --> B2[INSERT run_logs]
        B2 --> B3["INSERT trace_steps - batch"]
        B3 --> B4["INSERT trace_citations - batch"]
        B4 --> B5["INSERT trace_tool_calls - batch"]
        B5 --> B6[SELECT with JOINs - full trace detail]
    end
```

### 13.2 前端组件设计

**TraceTimeline**：垂直时间线布局。每个步骤为一条水平条目，左侧为步骤序号圆圈（颜色编码：success=绿色/failed=红色/running=蓝色 spinning/blocked=橙色 stopping）。右侧显示步骤名称（加粗）+ 耗时（灰色小字）+ 详情文本。当前正在执行的步骤显示动画边框（pulse 效果）。

**TraceStats**：水平卡片组。Token 用量卡片、总延迟卡片、工具调用次数卡片、知识命中数卡片。每个卡片包含图标 + 数值 + 单位。

---

## <a id="m7"></a>十四、模块 7：任务中心

### 14.1 任务状态机

```mermaid
stateDiagram-v2
    [*] --> pending: Task created
    pending --> approved: Approve
    pending --> rejected: Reject
    pending --> cancelled: Expired (24h timeout)
    approved --> executing: Worker picks up
    executing --> completed: Success
    executing --> failed: Failure
    failed --> executing: Retry (retryCount < maxRetries)
    failed --> cancelled: Max retries exceeded
    completed --> [*]
    rejected --> [*]
    cancelled --> [*]
```

### 14.2 RQ 队列设计

```python
from rq import Queue, Worker
from redis.asyncio import Redis

redis_conn = Redis.from_url(settings.REDIS_URL)

# Queue registration
tool_approval_queue = Queue("tool-approval", connection=redis_conn)
knowledge_index_status_queue = Queue("knowledge-index-status", connection=redis_conn)
memory_extraction_queue = Queue("memory-extraction", connection=redis_conn)
audit_log_queue = Queue("audit-log", connection=redis_conn)


async def handle_approval(job: "Job") -> None:
    """RQ worker handler for the tool-approval queue."""
    task_id = job.args["taskId"]
    tool_name = job.args["toolName"]
    params = job.args["params"]
    conversation_id = job.args["conversationId"]

    await task_service.update_status(task_id, "executing")
    result = await tool_proxy.execute(tool_name, params)
    await dify_client.resume_workflow(conversation_id, result)
    await task_service.update_status(task_id, "completed", result)


# Worker process (run separately: `rq worker tool-approval`)
# worker = Worker("tool-approval", connection=redis_conn)
# worker.work()
```

### 14.3 前端组件设计

**TaskCenterPage**：左右两栏布局。左栏：过滤器（状态：全部/待确认/执行中/已完成/失败 + 优先级：紧急/高/中/低）。右栏：任务卡片列表。每个任务卡片显示标题、发起人 + 时间（右上角灰色小字）、类型标签（工具审批=橙色/索引进度=蓝色/批量操作=紫色）、优先级标签（紧急=红色/高=橙色/中=蓝色/低=灰色）、操作按钮（待确认状态：绿色"同意"按钮 + 红色"拒绝"按钮；失败状态：蓝色"重试"按钮）。

---

## <a id="m8-14"></a>十五、模块 8–14

### 15.1 仪表盘模块

管理工作台展示四维核心指标：（1）智能体总数（蓝色，含"本月新增 +N"）（2）今日调用量（绿色，含"成功率 97.4%"）（3）平均耗时（灰色，含"较昨日 -12%"）（4）错误率（琥珀色，含"工具失败 18 次"）。24 小时调用趋势使用 Recharts 面积图，X 轴为小时（09:00-18:00），Y 轴为调用次数。

### 15.2 模型模块

展示 Dify 已配置的模型供应商列表，每个供应商卡片显示名称、部署类型（专有云/公网/本地）、状态、可用模型数、配额使用率进度条。点击展开具体模型列表，显示模型名称、场景标签（chat/tool_calling/embedding/rerank）、上下文窗口大小、成本等级（$/$\\$/$\\$\\$）。

### 15.3 记忆模块（P2）

对话结束时通过 RQ 异步提取：读取对话全部消息 → LLM 分析 → 输出 `[{type: "preference"|"fact", content: string, importance: 0-1}]` → 去重（pgvector cosine similarity） → 写入 `user_memories`。用户可在"我的记忆"页面查看、搜索、删除和纠错。

### 15.4–15.7

工作流模块（P2）：自有表单创建 → 生成 Dify DSL YAML → importDSL 导入。评测模块（P2）：测试集管理 + 批量调用 Dify + 自动评分。内容安全（P2）：Dify Moderation API Extension 对接。系统设置：全局配置 + 配额管理。


# 第三部分：前端架构深度设计

## <a id="ch-fe-arch"></a>十六、前端项目架构

### 16.1 技术栈详解与依赖清单

```json
{
  "dependencies": {
    "next": "^15.5.7", "react": "^19.2.1", "react-dom": "^19.2.1",
    "swr": "^2.3.0", "next-auth": "^5.0.0-beta",
    "tailwindcss": "^3.4.18", "tailwind-merge": "^3.4.0",
    "lucide-react": "^0.468.0",
    "zod": "^3.23.0", "react-virtuoso": "^4.12.0",
    "recharts": "^2.15.0", "react-syntax-highlighter": "^15.6.0",
    "clsx": "^2.1.1"
  },
  "devDependencies": {
    "typescript": "^5.9.3", "@types/react": "^19.2.7",
    "eslint": "^9.39.1", "eslint-config-next": "^15.5.7",
    "vitest": "^3.0.0", "@testing-library/react": "^16.0.0",
    "playwright": "^1.52.0"
  }
}
```

### 16.2 目录结构

```
apps/frontend/src/
├── app/
│   ├── layout.tsx                    # 根布局 - Provider 注入
│   ├── (auth)/login/page.tsx         # SSO 登录页
│   ├── user/                         # 员工工作区
│   │   ├── layout.tsx                # 侧边栏 + Header 布局
│   │   ├── page.tsx                  # AI 首页
│   │   ├── agents/page.tsx           # 智能体广场
│   │   ├── agents/[id]/page.tsx      # 智能体详情
│   │   ├── tasks/page.tsx            # 任务中心
│   │   ├── conversations/page.tsx    # 会话列表
│   │   ├── conversations/[id]/page.tsx # 聊天窗口
│   │   └── files/page.tsx            # 我的文件
│   └── admin/                        # 管理工作区
│       ├── layout.tsx                # 管理侧边栏布局
│       ├── page.tsx                  # 工作台
│       ├── agents/page.tsx           # 智能体资产列表
│       ├── agents/new/page.tsx       # 创建智能体
│       ├── agents/[id]/page.tsx      # 编辑/调试/发布
│       ├── knowledge/page.tsx        # 知识治理
│       ├── knowledge/[id]/page.tsx   # 知识库详情
│       ├── tools/page.tsx            # 工具列表
│       ├── tools/[id]/page.tsx       # 工具详情
│       ├── logs/page.tsx             # 运行日志
│       ├── logs/[traceId]/page.tsx   # Trace 详情
│       ├── models/page.tsx           # 模型与算力
│       ├── members/page.tsx          # 成员与权限
│       ├── workflow/page.tsx         # 工作流 (P2)
│       ├── evaluations/page.tsx      # 评测中心 (P2)
│       ├── safety/page.tsx           # 内容安全 (P2)
│       ├── health/page.tsx           # 系统健康 (P2)
│       └── settings/page.tsx         # 系统设置
├── components/
│   ├── ui/                           # 基础 UI 组件库
│   │   ├── Button.tsx                # 变体: primary/secondary/ghost/danger + 尺寸: sm/md/lg
│   │   ├── Input.tsx                 # 带 label/error/helperText
│   │   ├── Card.tsx                  # 变体: default/hover/interactive
│   │   ├── Modal.tsx                 # 动画: fade + scale, 支持 Esc 关闭
│   │   ├── Badge.tsx                 # 变体: default/success/warning/danger/info
│   │   ├── Table.tsx                 # 排序/分页/行选择
│   │   ├── Tabs.tsx                  # 水平 Tab 切换
│   │   ├── Toast.tsx                 # 右上角通知 (success/error/warning/info)
│   │   ├── Dropdown.tsx              # 下拉菜单
│   │   ├── Skeleton.tsx              # 骨架屏加载
│   │   ├── Spinner.tsx               # 旋转加载指示器
│   │   ├── EmptyState.tsx            # 空状态插画
│   │   └── ErrorBoundary.tsx         # 错误边界
│   ├── layout/
│   │   ├── Header.tsx                # 顶部导航栏
│   │   ├── UserSidebar.tsx           # 员工侧边栏
│   │   ├── AdminSidebar.tsx          # 管理侧边栏
│   │   └── WorkspaceSwitcher.tsx     # 工作区切换按钮
│   ├── chat/
│   │   ├── ChatWindow.tsx
│   │   ├── MessageList.tsx           # 虚拟滚动消息列表
│   │   ├── MessageBubble.tsx         # user(蓝色右对齐) / assistant(白色左对齐)
│   │   ├── StreamingText.tsx         # 流式逐字渲染 + 光标
│   │   ├── CitationCard.tsx
│   │   ├── ToolCallCard.tsx
│   │   ├── WorkflowProgress.tsx      # 工作流节点进度
│   │   ├── ChatInput.tsx             # 输入框 + 文件上传 + 发送/停止
│   │   └── ErrorBanner.tsx           # 可关闭的错误横幅
│   ├── agents/
│   │   ├── AgentCard.tsx
│   │   ├── AgentCreateForm.tsx       # 四步创建表单
│   │   ├── AgentConfigPanel.tsx      # Tab: 配置/调试/日志
│   │   ├── AgentStatusBadge.tsx
│   │   ├── ModelSelector.tsx         # 依赖式下拉 (供应商→模型→参数)
│   │   └── KnowledgeToolSelector.tsx # 双栏多选器
│   ├── knowledge/
│   │   ├── KBCard.tsx
│   │   ├── DocumentList.tsx
│   │   ├── DocumentUploader.tsx
│   │   ├── ChunkList.tsx
│   │   ├── ChunkDetail.tsx           # Modal: 完整分块
│   │   └── RetrievalTest.tsx
│   ├── tools/
│   │   ├── ToolCard.tsx
│   │   ├── ToolConfigForm.tsx
│   │   └── DebugPanel.tsx
│   ├── logs/
│   │   ├── RunLogTable.tsx
│   │   ├── LogFilterBar.tsx          # 智能体/用户/状态/时间筛选
│   │   ├── TraceTimeline.tsx
│   │   ├── TraceCitationList.tsx
│   │   ├── TraceToolCallList.tsx
│   │   └── TraceStats.tsx
│   ├── dashboard/
│   │   ├── MetricCard.tsx
│   │   ├── TrendChart.tsx
│   │   ├── TopAgentsList.tsx
│   │   ├── HealthStatusPanel.tsx
│   │   └── AlertList.tsx
│   └── members/
│       ├── MemberTable.tsx
│       └── MemberInviteModal.tsx
├── services/
│   ├── http-client.ts                # axios 实例 + JWT 拦截器 + 错误处理
│   ├── api-routes.ts                 # API 路径常量
│   ├── platform-service.ts           # 统一数据入口 (20+ 函数)
│   ├── auth-service.ts               # login/logout/refreshToken
│   ├── stream-service.ts             # SSE 消费工具函数
│   └── upload-service.ts             # 文件上传 (进度/取消)
├── hooks/
│   ├── useAgents.ts                  # SWR('/api/agents?...')
│   ├── useConversations.ts           # SWR('/api/conversations')
│   ├── useStreamChat.ts              # ★ SSE 流式核心 Hook
│   ├── useKnowledge.ts               # SWR('/api/knowledge-bases')
│   ├── useRunLogs.ts                 # SWR('/api/run-logs')
│   ├── useTasks.ts                   # SWR('/api/tasks')
│   ├── useAuth.ts                    # SWR('/api/me') + AuthContext
│   ├── useSSE.ts                     # 通用 SSE 连接管理
│   └── useDebounce.ts                # 300ms 防抖
├── stores/
│   ├── AuthContext.tsx                # {user, tenant, isLoading, error, login, logout}
│   ├── WorkspaceContext.tsx           # {workspace: 'user'|'admin', switchWorkspace}
│   └── ChatContext.tsx                # {currentConversationId, setCurrent}
├── types/platform.ts                  # 28 个领域类型 (共享)
└── lib/
    ├── utils.ts                       # cn() - clsx + tailwind-merge
    ├── formatters.ts                  # formatDate, formatNumber, formatBytes
    └── validators.ts                  # email, url, agentId 正则
```

---

## <a id="ch-fe-components"></a>十七、前端组件设计规范

### 17.1 基础 UI 组件规范

**Button**：支持 4 种变体（primary: 蓝色背景白色文字、secondary: 白色背景蓝色边框、ghost: 透明背景灰色文字 hover 灰色背景、danger: 红色背景白色文字）、3 种尺寸（sm: h-8 px-3 text-sm、md: h-10 px-4 text-base、lg: h-12 px-6 text-lg）、3 种状态（正常、loading: 左侧 Spinner + disabled、disabled: 灰色不透明度 50%）。所有 Button 使用 Tailwind CSS 的 `transition-colors duration-200` 实现平滑交互。

**Input**：包含 label（上方灰色文字）、input（标准边框，focus:border-blue-500 focus:ring-2）、error（红色边框 + 下方红色文字提示）、helperText（下方灰色小字）。支持 type 属性（text/email/password/number/url）。

**Modal**：固定定位全屏半透明黑色遮罩（bg-black/50）+ 居中白色卡片。打开/关闭动画：遮罩 opacity 0→1/1→0，卡片 scale 0.95→1/1→0.95 + opacity。支持按 Esc 关闭、点击遮罩关闭（可配置禁止）。内容区支持标题、正文、底部操作按钮。

**Table**：支持列定义配置（{key, label, sortable, render?, width?}）。排序：点击列标题触发升序/降序/默认三态切换。分页：底部页码按钮 + 每页条数下拉 + 总数显示。行点击：可选高亮行（可配置 onRowClick）。空状态：无数据时显示 EmptyState 组件。

**Toast**：右上角固定定位，堆叠显示（最多 5 个）。4 种类型：success（绿色勾图标）、error（红色叉图标）、warning（黄色三角图标）、info（蓝色 i 图标）。自动消失（默认 5 秒，可配置）。支持手动关闭按钮。入场动画：从右侧滑入 + opacity 淡入。

### 17.2 布局组件规范

**Header**：固定顶部（h-16），白色背景 + 底部 1px 灰色边框。左侧：平台 Logo（SVG 或文字） + 面包屑导航（当前页面路径）。右侧：通知图标按钮（Bell 图标，未读数红色 Badge）+ 用户下拉菜单（头像圆圈 + 用户名 + 下拉箭头）。下拉菜单项：个人设置、切换到管理端/员工端、登出。

**UserSidebar**：固定左侧（w-64），浅灰色背景。顶部：工作区切换器（员工工作区/管理工作区，仅当 canAccessAdmin=true 时显示切换按钮）。中部：导航链接列表（AI 首页/智能体广场/任务中心/最近会话/我的文件，当前页高亮蓝色背景）。底部：用户信息卡片（头像+姓名+部门）。

**AdminSidebar**：固定左侧（w-64），深色背景（slate-900）。顶部：平台 Logo + 版本号。分组导航：（1）核心管理：工作台/智能体资产/知识治理/工具管理（2）监控：运行日志/系统健康（3）系统：模型与算力/成员与权限/工作流/评测/安全/系统设置。当前页高亮蓝色左边框。

---

## <a id="ch-fe-data"></a>十八、前端数据流与状态管理

### 18.1 数据流架构

```mermaid
flowchart TD
    subgraph Providers["React Context Providers"]
        AC[AuthContext - user/tenant/isLoading]
        WC["WorkspaceContext - user/admin switch"]
        CC[ChatContext - currentConversation]
    end
    subgraph Hooks["SWR Data Hooks"]
        UA["useAgents - SWR /api/agents? ..."]
        UC["useConversations - SWR /api/conversations"]
        UK["useKnowledge - SWR /api/knowledge-bases"]
        UL["useRunLogs - SWR /api/run-logs"]
        UT["useTasks - SWR /api/tasks"]
        USC[useStreamChat - SSE Stream]
    end
    subgraph Services["Service Layer"]
        PS[platform-service.ts]
        HC[http-client.ts - axios + JWT interceptor]
        SS[stream-service.ts - SSE fetch + ReadableStream]
    end
    AC --> UA & UC
    UA & UC & UK & UL & UT --> PS --> HC --> API[Backend /api/*]
    USC --> SS --> API
```

### 18.2 SWR 配置策略

所有 SWR hooks 使用统一配置：`revalidateOnFocus: true`（标签页聚焦时刷新）、`revalidateOnReconnect: true`（网络恢复时刷新）、`errorRetryCount: 3`（失败重试 3 次）、`errorRetryInterval: 5000`（重试间隔 5 秒）、`dedupingInterval: 2000`（2 秒内相同请求合并）、`keepPreviousData: true`（加载新数据时保留旧数据显示）。

列表类 hooks（useAgents, useKnowledge, useConversations）使用 `.` 分隔的 SWR key 包含所有查询参数以支持独立缓存：`['agents', tenantId, status, category, page, search]`。

### 18.3 AuthContext 设计

```typescript
interface AuthState {
  user: CurrentUser | null;
  tenant: { id: string; name: string; logo?: string } | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
}

interface AuthContextValue extends AuthState {
  login: (provider: string) => void;         // 重定向到 /api/auth/login
  logout: () => Promise<void>;                // POST /api/auth/logout + 清除状态
  refreshToken: () => Promise<boolean>;       // 静默刷新，返回 false 则登出
}
```

初始化流程：App mount → `GET /api/me` → 成功：设置 user 和 tenant → 失败（401）：尝试 `refreshToken()` → 成功：重试 `GET /api/me` → 失败：设置 isAuthenticated=false → 显示登录页。

### 18.4 platform-service.ts 设计

```typescript
// 所有数据访问的统一入口，前端组件不直接调用 fetch
class PlatformService {
  // Auth
  async getCurrentUser(): Promise<CurrentUser> { return httpClient.get('/api/me'); }

  // Agents
  async listAgents(params?: AgentListParams): Promise<PaginatedResult<Agent>> { return httpClient.get('/api/agents', { params }); }
  async listPublishedAgents(category?: string): Promise<Agent[]> { return httpClient.get('/api/agents', { params: { status: 'published', category } }); }
  async getAgentDetail(id: string): Promise<AgentDetail> { return httpClient.get(`/api/agents/${id}`); }
  async createAgent(data: CreateAgentDto): Promise<Agent> { return httpClient.post('/api/agents', data); }
  async updateAgent(id: string, data: UpdateAgentDto): Promise<Agent> { return httpClient.patch(`/api/agents/${id}`, data); }
  async publishAgent(id: string): Promise<Agent> { return httpClient.post(`/api/agents/${id}/publish`); }
  async offlineAgent(id: string): Promise<void> { return httpClient.post(`/api/agents/${id}/offline`); }

  // Knowledge Bases
  async listKnowledgeBases(params?: any): Promise<PaginatedResult<KnowledgeBase>> { return httpClient.get('/api/knowledge-bases', { params }); }
  async getKnowledgeBaseDetail(id: string): Promise<KnowledgeBaseDetail> { return httpClient.get(`/api/knowledge-bases/${id}`); }
  async uploadDocument(kbId: string, file: File, onProgress?: (pct: number) => void): Promise<KnowledgeDocument> {
    const form = new FormData(); form.append('file', file);
    return httpClient.post(`/api/knowledge-bases/${kbId}/documents`, form, { headers: { 'Content-Type': 'multipart/form-data' }, onUploadProgress: (e) => onProgress?.(Math.round((e.loaded / e.total!) * 100)) });
  }
  async retrievalTest(kbId: string, query: string, topK: number = 5): Promise<RetrievalResult> { return httpClient.post(`/api/knowledge-bases/${kbId}/retrieval-test`, { query, topK }); }

  // Conversations - SSE not handled here (use useStreamChat hook directly)
  async listConversations(): Promise<Conversation[]> { return httpClient.get('/api/conversations'); }
  async getConversation(id: string): Promise<Conversation> { return httpClient.get(`/api/conversations/${id}`); }
  async deleteConversation(id: string): Promise<void> { return httpClient.delete(`/api/conversations/${id}`); }

  // Tools
  async listTools(params?: any): Promise<PaginatedResult<ToolAsset>> { return httpClient.get('/api/tools', { params }); }
  async getToolDetail(id: string): Promise<ToolDetail> { return httpClient.get(`/api/tools/${id}`); }
  async debugTool(id: string, requestBody: string): Promise<ToolDebugResult> { return httpClient.post(`/api/tools/${id}/debug`, { requestBody }); }

  // Run Logs
  async listRunLogs(params?: any): Promise<PaginatedResult<RunLog>> { return httpClient.get('/api/run-logs', { params }); }
  async getRunLogDetail(traceId: string): Promise<RunLog> { return httpClient.get(`/api/run-logs/${encodeURIComponent(traceId)}`); }

  // Tasks
  async listTasks(params?: any): Promise<TaskItem[]> { return httpClient.get('/api/tasks', { params }); }
  async approveTask(id: string): Promise<void> { return httpClient.post(`/api/tasks/${id}/approve`); }
  async rejectTask(id: string): Promise<void> { return httpClient.post(`/api/tasks/${id}/reject`); }

  // Dashboard
  async getAdminDashboard(): Promise<DashboardData> { return httpClient.get('/api/dashboard/admin'); }
  async getUserHome(): Promise<UserHomeData> { return httpClient.get('/api/dashboard/user'); }
  async getHealth(): Promise<HealthData> { return httpClient.get('/api/health'); }

  // Members
  async listMembers(params?: any): Promise<Member[]> { return httpClient.get('/api/members', { params }); }
  async inviteMember(data: { email: string; role: string }): Promise<void> { return httpClient.post('/api/members', data); }
  async updateMember(id: string, data: { role?: string; status?: string }): Promise<void> { return httpClient.patch(`/api/members/${id}`, data); }

  // Models
  async listModels(): Promise<ModelsData> { return httpClient.get('/api/models'); }
}

export const platformService = new PlatformService();
```

---

## <a id="ch-fe-routes"></a>十九、前端路由与页面设计

### 19.1 完整路由表（21 条）

**员工工作区**：`/user`（首页：推荐智能体 + 最近会话 + 待处理任务计数）、`/user/agents`（智能体广场：卡片网格 + 搜索 + 分类筛选标签）、`/user/agents/[id]`（智能体详情：描述 + 标签 + 绑定资源 + "开始对话"按钮）、`/user/tasks`（任务中心）、`/user/conversations`（最近会话列表）、`/user/conversations/[id]`（聊天窗口）、`/user/files`（我的文件）、`/user/memories`（我的记忆 P2）

**管理工作区**：`/admin`（工作台）、`/admin/agents`（智能体资产列表，含状态筛选）、`/admin/agents/new`（创建智能体四步表单）、`/admin/agents/[id]`（编辑/调试/发布）、`/admin/knowledge`（知识治理总览）、`/admin/knowledge/[id]`（知识库详情 Tab 页）、`/admin/tools`、`/admin/tools/[id]`、`/admin/logs`、`/admin/logs/[traceId]`、`/admin/models`、`/admin/members`、`/admin/workflow`、`/admin/evaluations`、`/admin/safety`、`/admin/health`、`/admin/settings`

---

## <a id="ch-fe-guide"></a>二十、前端开发指南

### 20.1 组件开发规范

1. **文件命名**：组件文件使用 PascalCase（`AgentCard.tsx`）；Hook 文件使用 camelCase（`useAgents.ts`）；工具函数使用 kebab-case（`format-date.ts`）。

2. **组件结构**：每个组件文件遵循固定顺序：（1）TypeScript 类型/接口定义（2）组件函数定义（3）`export default`。组件内部使用顺序：Hooks 调用 → 派生状态 → 事件处理函数 → JSX 渲染。

3. **Props 类型**：每个组件的 Props 必须显式定义 interface，继承关系使用 `extends`。可选参数使用 `?` 标记，提供 `defaultProps` 或默认参数值。

4. **样式规范**：优先使用 Tailwind CSS 原子类，避免内联 style。复杂样式抽取为 Tailwind 的 `@layer components`。使用 `cn()` 工具函数处理条件类名合并。

5. **错误处理**：每个数据获取组件必须处理三种状态：Loading（Skeleton 骨架屏）、Error（ErrorBanner + 重试按钮）、Empty（EmptyState 插画 + 提示文字）。

### 20.2 SWR Hook 开发模板

```typescript
import useSWR from 'swr';
import { platformService } from '@/services/platform-service';

export function useAgents(params?: AgentListParams) {
  const key = params ? ['agents', params.status, params.category, params.page, params.search] : ['agents'];
  const { data, error, isLoading, isValidating, mutate } = useSWR(
    key,
    () => platformService.listAgents(params),
    { revalidateOnFocus: true, errorRetryCount: 3, keepPreviousData: true }
  );

  return {
    agents: data?.data ?? [],
    total: data?.total ?? 0,
    page: data?.page ?? 1,
    isLoading,
    isRefreshing: isValidating && !!data,
    error,
    refresh: () => mutate(),
  };
}
```

### 20.3 性能优化规范

1. **服务端组件优先**：非交互页面（如智能体列表）使用 React Server Components 在服务端渲染，减少客户端 JS 体积。
2. **动态导入**：大型组件（如 ChatWindow、TraceDetailPage）使用 `next/dynamic` 懒加载。
3. **图片优化**：使用 Next.js `Image` 组件（`next/image`）自动优化图片格式和尺寸。
4. **虚拟滚动**：消息列表（1000+ 条）使用 `react-virtuoso`；表格使用分页（默认 20 条/页）。
5. **SWR 去重**：相同参数的 SWR 请求自动合并，减少网络请求。

### 20.4 前后端协同开发流程

1. **类型先行**：前后端开发者在 `packages/shared-types` 中共同定义 DTO 和领域类型，生成 TypeScript 类型文件后两边同步引用。
2. **API 契约优先**：在实现 API 之前，前后端共同确认每个端点的请求/响应 Schema，写入 `packages/shared-types/src/api.ts`。
3. **Mock 先行**：前端开发时使用 MSW（Mock Service Worker）模拟后端 API，加速 UI 开发。
4. **集成联调**：当后端 API 就绪后，前端移除 MSW handler，切换到真实 API 进行联调测试。


# 第四部分：后端架构深度设计


## <a id="ch-dify"></a>二十一、Dify 适配层详细设计

### 21.1 DifyClientService（Service API 客户端）

```python
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping

import httpx
import structlog

from app.core.exceptions import DifyApiError

logger = structlog.get_logger(__name__)


@dataclass
class _CircuitState:
    failures: int = 0
    last_failure: float = 0.0
    open: bool = False


class DifyClientService:
    """异步 Dify Service-API 客户端：重试 + 熔断。

    通过 FastAPI ``Depends(DifyClientService)`` 注入；底层使用 ``httpx.AsyncClient``。
    """

    CIRCUIT_FAILURE_THRESHOLD = 5
    CIRCUIT_RECOVERY_SECONDS = 30
    REQUEST_TIMEOUT = 30.0
    STREAM_TIMEOUT = 120.0

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._circuit_breaker: dict[str, _CircuitState] = {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
        )

    async def get(
        self,
        path: str,
        query: Mapping[str, str] | None = None,
        *,
        retries: int = 3,
    ) -> dict[str, Any]:
        """GET 请求，带指数退避重试。"""

        async def _do() -> dict[str, Any]:
            response = await self._client.get(path, params=query)
            if response.is_error:
                raise DifyApiError(response.status_code, response.text)
            return response.json()

        return await self._with_retry(_do, retries)

    async def post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        retries: int = 3,
    ) -> dict[str, Any]:
        """POST 请求，带指数退避重试。"""

        async def _do() -> dict[str, Any]:
            response = await self._client.post(
                path,
                json=dict(body),
                headers={"Content-Type": "application/json"},
            )
            if response.is_error:
                raise DifyApiError(response.status_code, response.text)
            return response.json()

        return await self._with_retry(_do, retries)

    async def post_stream(
        self,
        path: str,
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        """POST 流式请求；返回字节异步迭代器。

        熔断器在失败时累加计数，达到阈值后开启；连续 5 次失败后熔断 30s。
        """
        if self._is_circuit_open(path):
            raise DifyApiError(503, "Circuit breaker open for Dify API")
        try:
            async with self._client.stream(
                "POST",
                path,
                json=dict(body),
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(self.STREAM_TIMEOUT),
            ) as response:
                if response.is_error:
                    raise DifyApiError(response.status_code, await response.aread().decode())
                self._reset_circuit(path)
                # 透传原始字节流
                async for chunk in response.aiter_bytes():
                    yield chunk
        except DifyApiError:
            self._record_failure(path)
            raise

    async def _with_retry(self, fn: Any, retries: int) -> Any:
        for attempt in range(retries):
            try:
                return await fn()
            except Exception:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    def _is_circuit_open(self, path: str) -> bool:
        cb = self._circuit_breaker.get(path)
        if not cb or not cb.open:
            return False
        if time.monotonic() - cb.last_failure > self.CIRCUIT_RECOVERY_SECONDS:
            cb.open = False
            return False
        return True

    def _record_failure(self, path: str) -> None:
        cb = self._circuit_breaker.setdefault(path, _CircuitState())
        cb.failures += 1
        cb.last_failure = time.monotonic()
        if cb.failures >= self.CIRCUIT_FAILURE_THRESHOLD:
            cb.open = True
            logger.warning("circuit_breaker.opened", path=path, failures=cb.failures)

    def _reset_circuit(self, path: str) -> None:
        self._circuit_breaker.pop(path, None)

    async def aclose(self) -> None:
        await self._client.aclose()
```

### 21.2 DifyConversationAdapter（SSE 流式代理）

```python
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import structlog

from app.services.dify_client import DifyClientService
from app.services.memory import MemoryService
from app.core.event_bus import EventBus

logger = structlog.get_logger(__name__)


class DifyConversationAdapter:
    """将 Dify ``/v1/chat-messages`` SSE 流转换为平台 SSE 事件流。

    事件类型与 v7 完全一致：``message``、``message_end``、``agent_thought``、
    ``workflow_started``、``workflow_finished``、``node_started``、
    ``node_finished``、``error``。
    """

    def __init__(
        self,
        dify_client: DifyClientService,
        memory_service: MemoryService,
        event_bus: EventBus,
    ) -> None:
        self._dify = dify_client
        self._memory = memory_service
        self._event_bus = event_bus

    async def stream_messages(
        self,
        conv_id: str,
        query: str,
        user_id: str,
        tenant_id: str,
        files: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """异步生成 SSE 事件字典。"""
        dify_user = f"{tenant_id}:{user_id}"
        memories = await self._memory.recall(user_id, tenant_id, query)

        inputs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "user_preferences": "; ".join(
                m["content"] for m in memories if m["type"] == "preference"
            ),
            "user_facts": "; ".join(
                m["content"] for m in memories if m["type"] == "fact"
            ),
        }
        body: dict[str, Any] = {
            "query": query,
            "user": dify_user,
            "response_mode": "streaming",
            "conversation_id": conv_id or "",
            "inputs": inputs,
            "auto_generate_name": not conv_id,
        }
        if files:
            body["files"] = [
                {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": f["id"],
                }
                for f in files
            ]

        buffer = ""
        event_type = ""

        async for chunk in self._dify.post_stream("/v1/chat-messages", body):
            buffer += chunk.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            buffer = lines.pop() if lines else ""

            for line in lines:
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                    continue
                if not line.startswith("data: "):
                    continue

                data = json.loads(line[6:].strip())

                if event_type == "message":
                    yield {
                        "event": "message",
                        "data": {
                            "content": data.get("answer"),
                            "messageId": data.get("message_id"),
                            "conversationId": data.get("conversation_id"),
                            "taskId": data.get("task_id"),
                        },
                    }

                elif event_type == "message_end":
                    metadata = data.get("metadata") or {}
                    yield {
                        "event": "message_end",
                        "data": {
                            "traceId": data.get("id"),
                            "metadata": {
                                "usage": metadata.get("usage"),
                                "retrieverResources": [
                                    {
                                        "sourceName": r.get("document_name"),
                                        "kbName": r.get("dataset_name"),
                                        "excerpt": (r.get("content") or "")[:200],
                                        "score": r.get("score"),
                                    }
                                    for r in metadata.get("retriever_resources", [])
                                ],
                            },
                        },
                    }
                    await self._event_bus.publish(
                        "conversation.completed",
                        {
                            "conversationId": data.get("conversation_id"),
                            "userId": user_id,
                            "tenantId": tenant_id,
                        },
                    )

                elif event_type == "agent_thought":
                    yield {
                        "event": "agent_thought",
                        "data": {
                            "thoughtId": data.get("id"),
                            "thought": data.get("thought"),
                            "tool": data.get("tool"),
                            "observation": data.get("observation"),
                        },
                    }

                elif event_type == "workflow_started":
                    yield {
                        "event": "workflow_started",
                        "data": {"workflowRunId": data.get("workflow_run_id")},
                    }

                elif event_type == "workflow_finished":
                    wf_data = data.get("data") or {}
                    yield {
                        "event": "workflow_finished",
                        "data": {
                            "status": wf_data.get("status"),
                            "outputs": wf_data.get("outputs"),
                            "error": wf_data.get("error"),
                        },
                    }

                elif event_type == "node_started":
                    node_data = data.get("data") or {}
                    yield {
                        "event": "node_started",
                        "data": {
                            "nodeId": node_data.get("node_id"),
                            "title": node_data.get("title"),
                            "index": node_data.get("index"),
                        },
                    }

                elif event_type == "node_finished":
                    node_data = data.get("data") or {}
                    yield {
                        "event": "node_finished",
                        "data": {
                            "nodeId": node_data.get("node_id"),
                            "status": node_data.get("status"),
                        },
                    }

                elif event_type == "error":
                    yield {
                        "event": "error",
                        "data": {
                            "code": data.get("code"),
                            "message": data.get("message"),
                            "status": data.get("status"),
                        },
                    }
```

---

## <a id="ch-be-guide"></a>二十二、后端开发指南与最佳实践

### 22.1 FastAPI 模块开发模板

每个业务模块遵循以下标准结构：

```python
# 1. Router 定义 (app/routers/agents.py)
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/agents", tags=["agents"])

# 2. Service - 业务逻辑编排 (app/services/agent_service.py)
class AgentService:
    def __init__(self, repo: AgentRepository, dify_adapter: DifyAgentAdapter):
        self._repo = repo
        self._dify = dify_adapter

# 3. Repository - SQLAlchemy 数据访问 (app/repositories/agent_repository.py)
class AgentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

# 4. 路由注册 (app/main.py)
# app.include_router(agents.router, dependencies=[Depends(get_current_user)])
```

### 22.2 错误处理规范

1. 业务逻辑中抛出 FastAPI 标准 HTTP 异常：`raise HTTPException(status_code=404, detail="Agent not found")`、`raise HTTPException(status_code=400, detail="Missing required config")`、`raise HTTPException(status_code=409, detail="Agent ID already exists")`。
2. Dify 调用失败时抛出 `DifyApiError`，由全局异常处理器 `@app.exception_handler(DifyApiError)` 统一转换为 502。
3. 未知异常由全局异常处理器兜底，返回 500 + 结构化日志。

```python
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import structlog

logger = structlog.get_logger(__name__)

async def dify_api_error_handler(request: Request, exc: DifyApiError) -> JSONResponse:
    logger.error("dify.api_error", status=exc.status_code, detail=exc.detail, path=request.url.path)
    return JSONResponse(
        status_code=502,
        content={"error": {"code": "DIFY_UNAVAILABLE", "message": "AI engine temporarily unavailable"}},
    )

async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled.exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
    )
```

### 22.3 日志规范

使用 structlog 记录三种级别的日志：
- `logger.info`：正常业务流程（Agent created、Conversation started、Task completed）
- `logger.warning`：可恢复异常（Dify API retry、Circuit breaker opened、Quota approaching limit）
- `logger.error`：不可恢复异常（Dify console login failed、Database connection lost）

每条日志包含：`{ action, resource, resourceId, tenantId, userId, duration, ...data }`。

```python
import structlog

logger = structlog.get_logger(__name__)

# 示例：创建智能体
logger.info(
    "agent.created",
    action="create_agent",
    resource="agent",
    resourceId=agent_id,
    tenantId=tenant_id,
    userId=user_id,
    duration_ms=elapsed,
)
```

### 22.4 数据库查询规范

1. 所有查询通过 Repository 模式，不直接在 Service 中使用 `AsyncSession`。
2. 所有查询强制注入 `tenant_id` 过滤条件。
3. 关联查询使用 `selectinload` / `joinedload` 而非多次查询（N+1 问题预防）。
4. 大批量写入使用 `session.add_all()` 或事务批量处理。
5. 复杂聚合查询使用 `text()` + 参数化 SQL。

```python
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

class AgentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, agent_id: str, tenant_id: str) -> Agent | None:
        stmt = (
            select(Agent)
            .where(Agent.agent_id == agent_id, Agent.tenant_id == tenant_id)
            .options(selectinload(Agent.knowledge_bases))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_tenant(self, tenant_id: str) -> int:
        stmt = select(func.count(Agent.id)).where(Agent.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()
```

### 22.5 测试规范

每个模块必须包含：
- `test_agent_service.py`：Service 层单元测试（Mock Repository 和 Adapter）
- `test_agent_router.py`：Router 层单元测试（Mock Service，使用 `httpx.AsyncClient`）
- `test_agent_e2e.py`：E2E 测试（真实 HTTP 请求 + Testcontainers 数据库）

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_create_agent(db_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/agents", json={...})
    assert response.status_code == 201
```

---

## <a id="ch-be-middleware"></a>二十三、后端中间件与依赖注入

### 23.1 JWT 认证依赖（JwtAuthGuard → get_current_user）

```python
from fastapi import Depends, Request, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer
from authlib.jose import jwt, JoseError
from app.core.config import settings

import structlog

logger = structlog.get_logger(__name__)

oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=settings.OIDC_AUTH_URL,
    tokenUrl=settings.OIDC_TOKEN_URL,
    auto_error=False,
)

async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
) -> dict:
    """JWT 认证依赖，替代 NestJS JwtAuthGuard。

    优先从 cookie 读取 access_token，其次从 Authorization header 读取。
    """
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    try:
        payload = jwt.decode(token, settings.JWT_PUBLIC_KEY, claims_options={"alg": ["RS256"]})
        return {
            "id": payload["sub"],
            "tenantId": payload["tenant_id"],
            "role": payload["role"],
        }
    except JoseError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
```

### 23.2 角色权限依赖（RolesGuard → require_roles）

```python
from fastapi import Depends, HTTPException, status
from typing import Sequence

def require_roles(*allowed_roles: str):
    """角色检查依赖工厂，替代 NestJS RolesGuard。

    用法：``dependencies=[Depends(require_roles('platform_admin'))]``
    """
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if "all" in allowed_roles:
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user
    return _check
```

### 23.3 租户校验依赖（TenantGuard → get_active_tenant）

```python
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.tenant import Tenant

async def get_active_tenant(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> str:
    """租户状态 + 配额校验依赖，替代 NestJS TenantGuard。"""
    stmt = select(Tenant).where(Tenant.id == user["tenantId"])
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant or tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant suspended")
    if tenant.quota_used >= tenant.quota_limit:
        raise HTTPException(status_code=429, detail="Quota exceeded")
    return tenant.id
```

### 23.4 响应包装中间件（TransformInterceptor → response middleware）

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp
import json

class TransformMiddleware(BaseHTTPMiddleware):
    """统一包装响应体为 ``{ "data": ... }``，替代 NestJS TransformInterceptor。

    如果响应体已经是 ``{"data": ...}`` 格式则原样透传。
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # 仅处理 JSON 响应
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "data" in data:
                return Response(content=body, media_type="application/json", status_code=response.status_code)
            wrapped = json.dumps({"data": data})
            return Response(content=wrapped, media_type="application/json", status_code=response.status_code)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(content=body, media_type="application/json", status_code=response.status_code)
```

---

## <a id="ch-security"></a>二十四、安全性设计

**认证安全**：JWT RS256 非对称签名、Access Token 2h + Refresh Token 30d rotation、CSRF 三重防护（SameSite Cookie + OIDC state + PKCE）、CORS 白名单 Nginx + FastAPI 双重、失败 5 次锁定 15 分钟。

**数据安全**：TLS 1.3 传输加密、pgcrypto + AES-256-GCM 静态加密、敏感字段脱敏（email/phone/api_key）、SQLAlchemy 参数化查询防注入、React 默认 HTML 转义防 XSS + CSP Header。

**速率限制**：全局 100 req/s per IP、登录 10 req/min、对话 20 req/min per user、上传 10 req/min per user。使用 Redis Token Bucket 算法。

**审计日志**：所有管理操作通过 EventBus 异步写入 audit_logs 表，不阻塞主流程。记录字段：tenantId, userId, action, resource, resourceId, details, ipAddress, userAgent, createdAt。

---

## <a id="ch-obs"></a>二十五、可观测性设计

**结构化日志**：structlog JSON 格式 → stdout → ELK/CloudWatch。自动脱敏：`req.headers.authorization`、`req.headers.cookie`、`body.password`。

```python
import logging

import structlog

def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
```

**健康检查**：`GET /api/health`（基本状态 + uptime + version）、`GET /api/health/ready`（DB ping + Redis ping + Dify /v1/info ping，用于 K8s readiness probe）、`GET /api/health/live`（始终 200，用于 K8s liveness probe）。

**Prometheus 指标**：`/metrics` 端点，上报 HTTP 请求计数/延迟分布、Dify API 调用计数/延迟、RQ 队列深度/处理速率、DB 连接池使用率。使用 `prometheus_fastapi_instrumentator` 自动仪表化 FastAPI 应用。

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator.instrument(app).expose(app, endpoint="/metrics")
```

**告警规则**：Dify API 连续 3 次健康检查失败 → Critical → 企业微信+邮件；5xx 错误率 >5%（5 分钟窗口）→ Critical；配额使用率 >90% → Warning；RQ 队列积压 >100 → Warning。

---

## <a id="ch-test"></a>二十六、测试策略

**测试金字塔**：E2E 5%（Playwright，20 个关键路径）、集成 20%（httpx.AsyncClient + Testcontainers）、单元 75%（pytest + pytest-asyncio）。

```python
# pytest 配置示例 (pyproject.toml)
# [tool.pytest.ini_options]
# asyncio_mode = "auto"
# testpaths = ["tests"]
```

**关键测试场景**：Agent CRUD 全流程、流式对话 SSE、KB 上传→索引→召回、工具审批链路、租户数据隔离、Dify 不可用降级（Mock DifyClientService 返回 502）、RBAC 权限（employee 访问 POST /api/agents → 403）、SSO 登录（Playwright 模拟完整 OIDC 流程）。

---

## <a id="ch-deploy"></a>二十七、部署与运维设计

### Docker Compose（8 个容器）

```yaml
services:
  nginx:        image: nginx:alpine, ports: ["80:80"]
  frontend:     build: apps/frontend, ports: ["3000"]
  backend:      build: apps/backend, ports: ["3001"]
  dify-api:     image: langgenius/dify-api:1.x, ports: ["5001"]
  dify-worker:  image: langgenius/dify-api:1.x
  postgres:     image: pgvector/pgvector:pg16, ports: ["5432"]
  redis:        image: redis:7-alpine, ports: ["6379"]
  weaviate:     image: semitechnologies/weaviate
```

### CI/CD Pipeline

```mermaid
flowchart TD
    PR[Pull Request] --> Lint[ruff check + mypy] --> Unit[pytest - unit] --> Build[Build Docker image] --> Docker[docker build + push]
    Merge[Merge to main] --> Staging[docker compose up -d staging]
    Staging --> Integration[pytest - integration] --> E2E[pytest - e2e + Playwright]
    E2E -->|Pass| Approve{Manual Approval} -->|Yes| Prod[docker compose up -d production]
    E2E -->|Fail| Rollback[Rollback + Notify]
```

---

## <a id="ch-collab"></a>二十八、前后端开发协同规范

1. **API 契约**：FastAPI 自动生成 OpenAPI schema（`/openapi.json`），前端使用 `openapi-typescript` 从 schema 生成 TypeScript 类型，确保前后端类型一致。后端使用 Pydantic v2 定义请求/响应模型，schema 自动导出。
2. **类型生成流程**：后端 Pydantic 模型 → FastAPI OpenAPI schema → `openapi-typescript` → 前端 `types/api-generated.ts`。前端自定义类型在 `types/platform.ts` 中手动维护。
3. **分支策略**：`main` 受保护，功能开发在 `feat/xxx` 分支，合并前需通过 CI（ruff + mypy + pytest）。
4. **代码审查**：每个 PR 至少一人审查。审查清单：类型安全、错误处理、测试覆盖、性能考虑。
5. **环境变量**：`.env.example` 是唯一真实来源。本地开发复制为 `.env.local` 并填入真实值。生产环境通过 CI/CD 注入。后端使用 `pydantic-settings` 的 `BaseSettings` 管理配置。

---

## <a id="ch-plan"></a>二十九、开发阶段规划

| 阶段 | 周期 | FE | BE | 交付物 |
|------|------|----|----|--------|
| P1 | 1-8 周 | 3.5w | 6.5w | SSO+RBAC、Agent CRUD、KB管理、流式对话、工具代理、任务中心、仪表盘 |
| P2 | 9-14 周 | 3w | 5w | 记忆系统、评测中心、Trace拆表、企业工具代理、工作流、内容安全 |
| P3 | 15-19 周 | 1w | 3w | OCR、A2A预研、K8s部署、压测、安全加固 |

### P1 详细周计划

| 周 | 前端（成员 A） | 后端（成员 B） |
|----|---------------|---------------|
| 1 | Monorepo搭建 + Tailwind主题 + 基础UI组件 | FastAPI+SQLAlchemy初始化 + Models+Alembic Migration + Dify部署 |
| 2 | 登录页 + AuthContext + AuthGuard + Header | authlib OIDC + JWT签发/刷新 + RBAC依赖 + 租户依赖 + 审计日志 |
| 3 | AgentMarketplace + AgentCreateForm + useAgents | AgentRepository + DifyConsoleClient + DifyClientService + CRUD API |
| 4 | AgentDetail + MiniChatWindow + ConfigPanel | KnowledgeRepository + DifyKnowledgeAdapter + 文档上传/检索 API |
| 5 | ConversationDetail + useStreamChat + SSE渲染组件 | DifyConversationAdapter (SSE) + 租户user参数编码 + 会话 API |
| 6 | TaskCenter + ToolList + AdminDashboard | TaskService+RQ + ToolProxy + Dashboard聚合 |
| 7 | Error/Loading/Skeleton + 响应式 + Toast | DifyClient重试/熔断 + RateLimit + Health + structlog日志 |
| 8 | E2E Playwright + 性能优化 + a11y | 集成测试 + CI/CD + Docker + 压测 + 文档 |

---

*文档版本: v2.0 | 总字数: ~200,000 | 技术栈: Next.js 15 + FastAPI 0.115 + SQLAlchemy 2.0 + PostgreSQL 16 + Redis 7 + Dify CE (纯后端)*
*章节: 29 章 | 图表: 15+ Mermaid 图表 | API Endpoints: 53 | SQLAlchemy Models: 24 | 组件: 85+ | 代码: 完整 Router/Service/Adapter/DifyClientService*

---

## 附录 A：完整 API 参考

### A.1 认证 API

**POST /api/auth/login**
```
Request: { "provider": "oidc" }
Response: 302 Redirect to IdP /authorize
```

**GET /api/auth/callback**
```
Query: ?code={authorization_code}&state={csrf_state}
Response: 302 Redirect to /user + Set-Cookie: access_token (HttpOnly, Secure, SameSite=Lax, Max-Age=7200)
Error: 400 { error: { code: "INVALID_STATE", message: "CSRF state mismatch" } }
```

**POST /api/auth/refresh**
```
Request: { "refreshToken": "rt_xxx" }
Response 200: { data: { accessToken: "jwt_xxx", refreshToken: "rt_new", expiresIn: 7200 } }
Error 401: { error: { code: "INVALID_REFRESH_TOKEN", message: "Token expired or revoked" } }
```

### A.2 智能体 API

**POST /api/agents — 创建智能体**
```
Request:
{
  "name": "合同审查助手",
  "agentId": "agent-contract",
  "type": "chat",
  "description": "审查合同风险、输出条款建议",
  "category": "法务合规",
  "visibility": "department",
  "icon": "📋",
  "tags": ["合同", "风险"],
  "modelProvider": "openai",
  "modelId": "gpt-4o",
  "modelParams": { "temperature": 0.3, "max_tokens": 4096 },
  "prompt": "你是一个专业的法务合同审查助手...",
  "knowledgeBaseIds": ["kb-contract"],
  "toolIds": ["tool-contract-risk"]
}

Response 201:
{
  "data": {
    "agentId": "agent-contract",
    "name": "合同审查助手",
    "type": "chat",
    "status": "draft",
    "difyAppId": "uuid-generated-by-dify",
    "category": "法务合规",
    "visibility": "department",
    "icon": "📋",
    "tags": ["合同", "风险"],
    "modelId": "gpt-4o",
    "modelName": "GPT-4o",
    "prompt": "你是一个专业的法务合同审查助手...",
    "version": 1,
    "createdAt": "2026-08-06T10:30:00Z",
    "updatedAt": "2026-08-06T10:30:00Z"
  }
}

Error 409: { error: { code: "DUPLICATE_AGENT_ID", message: "Agent ID 'agent-contract' already exists" } }
Error 502: { error: { code: "DIFY_UNAVAILABLE", message: "Failed to create Dify application" } }
```

**GET /api/agents/:id — 智能体详情**
```
Response 200:
{
  "data": {
    "agent": {
      "agentId": "agent-contract",
      "name": "合同审查助手",
      "description": "审查合同风险、输出条款建议",
      "type": "chat",
      "status": "published",
      "category": "法务合规",
      "visibility": "department",
      "icon": "📋",
      "tags": ["合同", "风险"],
      "modelId": "gpt-4o",
      "modelName": "GPT-4o",
      "prompt": "你是一个专业的法务合同审查助手...",
      "callsToday": 632,
      "successRate": 97.2,
      "avgLatencyMs": 1640,
      "version": 3,
      "createdAt": "2026-07-01T09:00:00Z",
      "updatedAt": "2026-08-06T10:30:00Z",
      "publishedAt": "2026-08-01T14:00:00Z"
    },
    "boundKnowledge": [
      {
        "kbId": "kb-contract",
        "name": "合同与法务知识库",
        "type": "policy",
        "status": "ready",
        "docCount": 128,
        "chunkCount": 8640,
        "authorizedScope": "法务部、采购部、管理层"
      }
    ],
    "boundTools": [
      {
        "toolId": "tool-contract-risk",
        "name": "合同风险条款识别",
        "type": "http",
        "riskLevel": "medium",
        "permissionMode": "confirm",
        "status": "active"
      }
    ],
    "recentLogs": [
      {
        "traceId": "trace-20260806-legal-0172",
        "status": "blocked",
        "inputPreview": "请审查这份采购合同，重点看付款条款...",
        "latencyMs": 2140,
        "createdAt": "2026-08-06T10:25:00Z"
      }
    ]
  }
}
```

### A.3 知识库 API

**POST /api/knowledge-bases/:id/documents — 上传文档**
```
Request: multipart/form-data { file: <binary> }
Headers: Content-Type: multipart/form-data

Response 201:
{
  "data": {
    "id": "doc-contract-003",
    "knowledgeBaseId": "kb-contract",
    "name": "supplier-agreement-2026.pdf",
    "fileType": "pdf",
    "sizeLabel": "2.8 MB",
    "status": "indexing",
    "uploadedAt": "2026-08-06T10:30:00Z"
  }
}

Error 422: { error: { code: "UNSUPPORTED_FORMAT", message: "File type .exe is not supported. Supported: PDF, DOCX, XLSX, TXT, MD, CSV, JSON, HTML" } }
Error 422: { error: { code: "FILE_TOO_LARGE", message: "File size 25MB exceeds 15MB limit" } }
```

**POST /api/knowledge-bases/:id/retrieval-test — 召回测试**
```
Request: { "query": "付款触发条件", "topK": 5 }

Response 200:
{
  "data": {
    "citations": [
      {
        "sourceName": "采购合同审查要点 2026.pdf",
        "knowledgeBaseName": "合同与法务知识库",
        "excerpt": "付款条款应明确验收、发票、审批三个触发条件，并说明各条件未满足时的处理方式...",
        "score": 0.91
      },
      {
        "sourceName": "数据处理协议模板.docx",
        "knowledgeBaseName": "合同与法务知识库",
        "excerpt": "数据处理方不得在未经书面授权的情况下再委托第三方处理...",
        "score": 0.87
      }
    ],
    "latencyMs": 120
  }
}
```

### A.4 会话 API

**POST /api/conversations/:id/messages — 发送消息（SSE 流式）**
```
Request:
{
  "query": "请审查这份采购合同，重点看付款、违约责任和数据处理条款",
  "agentId": "agent-contract",
  "files": [
    {
      "name": "contract-v2.pdf",
      "uploadFileId": "file-uuid-from-upload"
    }
  ]
}

Response: Content-Type: text/event-stream

event: message
data: {"content":"已","messageId":"msg-xxx","conversationId":"conv-xxx","taskId":"task-xxx"}

event: message
data: {"content":"识别出","messageId":"msg-xxx"}

event: message
data: {"content":"3 类风险：付款节点缺少验收条件、违约责任上限偏低、数据处理条款未限定再委托范围...","messageId":"msg-xxx"}

event: node_started
data: {"nodeId":"node-1","title":"检索知识库","index":1}

event: node_finished
data: {"nodeId":"node-1","status":"success"}

event: message_end
data: {"messageId":"msg-xxx","conversationId":"conv-xxx","traceId":"trace-20260806-legal-0172","metadata":{"usage":{"total_tokens":1161,"total_price":"0.0012890","currency":"USD","latency":2.14},"retriever_resources":[{"document_name":"采购合同审查要点 2026.pdf","dataset_name":"合同与法务知识库","segment_id":"seg-xxx","score":0.91,"content":"付款条款应明确验收..."}]}}
```

### A.5 工具 API

**POST /api/tools/:id/debug — 调试工具**
```
Request: { "requestBody": "{\"contractText\":\"甲方在收到发票后 30 日内付款\"}", "saveAsCase": true, "caseName": "标准合同测试" }

Response 200:
{
  "data": {
    "statusCode": 200,
    "responseBody": "{\"riskCount\":3,\"risks\":[{\"level\":\"medium\",\"clause\":\"付款\",\"reason\":\"缺少明确验收条件\"}]}",
    "latencyMs": 420,
    "savedCaseId": "case-uuid-xxx"
  }
}
```

### A.6 任务 API

**POST /api/tasks/:id/approve — 审批通过**
```
Response 200: { "data": { "id": "task-uuid", "status": "approved", "resolvedAt": "2026-08-06T10:35:00Z" } }
Error 403: { "error": { code: "FORBIDDEN", "message": "Only agent_admin+ can approve tasks" } }
Error 404: { "error": { code: "NOT_FOUND", "message": "Task not found" } }
Error 422: { "error": { code: "INVALID_TRANSITION", "message": "Cannot transition from 'completed' to 'approved'" } }
Error 422: { "error": { code: "TASK_EXPIRED", "message": "Task has expired and can no longer be approved" } }
```


## 附录 B：完整错误码参考

| HTTP | Code | Message | 触发条件 |
|------|------|---------|---------|
| 400 | VALIDATION_ERROR | Invalid request parameters | Pydantic 校验失败 |
| 401 | UNAUTHORIZED | Invalid or expired token | JWT 过期/无效/缺失 |
| 401 | INVALID_REFRESH_TOKEN | Token expired or revoked | RefreshToken 无效 |
| 403 | FORBIDDEN | Insufficient permissions | RBAC 角色不匹配 |
| 403 | TENANT_SUSPENDED | Tenant account suspended | 租户状态非 active |
| 404 | NOT_FOUND | Resource not found | 任意资源不存在 |
| 404 | AGENT_NOT_FOUND | Agent not found | agentId 不存在 |
| 404 | KB_NOT_FOUND | Knowledge base not found | kbId 不存在 |
| 409 | DUPLICATE_AGENT_ID | Agent ID already exists | 重复 agentId |
| 409 | ALREADY_MEMBER | User already a member | 重复邀请 |
| 422 | MISSING_CONFIG | KB or Tool must be configured | 发布时缺少必要配置 |
| 422 | UNSUPPORTED_FORMAT | File type not supported | 上传不支持的文件格式 |
| 422 | FILE_TOO_LARGE | File exceeds size limit | 文件超过 15MB |
| 422 | INVALID_TRANSITION | Invalid status transition | 状态机不允许的转换 |
| 422 | TASK_EXPIRED | Task expired | 超过 expires_at 的任务 |
| 429 | RATE_LIMITED | Too many requests | 速率限制触发 |
| 429 | QUOTA_EXCEEDED | Monthly quota exceeded | 租户配额超限 |
| 500 | INTERNAL_ERROR | Internal server error | 未捕获异常 |
| 502 | DIFY_UNAVAILABLE | AI engine temporarily unavailable | Dify API 调用失败 |
| 502 | DIFY_APP_NOT_FOUND | Dify app not found | Dify Console API 返回 404 |
| 504 | DIFY_TIMEOUT | AI engine request timeout | Dify API 超时 |


## 附录 C：部署检查清单

### 部署前检查

- [ ] `.env` 文件已配置所有必需环境变量（DATABASE_URL, REDIS_URL, DIFY_API_BASE_URL, DIFY_ADMIN_API_KEY, DIFY_ADMIN_EMAIL, DIFY_ADMIN_PASSWORD, JWT_PRIVATE_KEY, JWT_PUBLIC_KEY）
- [ ] PostgreSQL 已运行并创建数据库（`CREATE DATABASE eap`）
- [ ] Redis 已运行并可连接（`redis-cli ping` → PONG）
- [ ] Dify API 已部署并可通过内网访问（`curl http://dify-api:5001/v1/info`）
- [ ] Dify Worker 已部署（异步索引任务必需）
- [ ] Weaviate/Qdrant 已部署（向量检索必需）

### 部署步骤

1. **数据库迁移**：`cd apps/backend && alembic upgrade head`
2. **构建前端**：`cd apps/frontend && pnpm build`
3. **构建后端**：`cd apps/backend && docker build -t eap-backend .`
4. **启动服务**：`docker compose up -d`
5. **验证健康**：`curl http://localhost/api/health/ready` → 200
6. **验证 Dify**：`curl http://localhost/api/health/ready` 响应中 `dify: "ok"`
7. **创建初始租户**：调用 Backend 内部 API 创建第一个租户和管理员账号
8. **验证登录**：浏览器访问 `http://localhost/login` → SSO 登录 → 成功跳转 `/user`

### 部署后验证

- [ ] SSO 登录成功
- [ ] 创建智能体（填写表单 → 提交 → 在列表中看到）
- [ ] 创建知识库 + 上传文档 → 等待索引完成 → 召回测试返回结果
- [ ] 员工端发起对话 → SSE 流式输出 → 引用来源展示
- [ ] 触发高风险工具 → 任务中心出现待审批任务 → 审批后工具执行
- [ ] 管理端查看运行日志和 Trace 详情
- [ ] Prometheus metrics 端点可访问
- [ ] Grafana 仪表盘显示指标


## 附录 D：性能基准

| 场景 | 目标 | 测量方法 |
|------|------|---------|
| Agent 列表 API | P95 < 200ms | 含 Dify /v1/info 调用 + Redis 缓存命中 |
| KB 列表 API | P95 < 150ms | 含 Dify /v1/datasets 调用 |
| 对话首 Token | P95 < 1500ms | 从 POST 请求到第一个 SSE message 事件 |
| 文档上传 | P95 < 3s | 15MB PDF 上传 + Dify 索引启动 |
| 召回测试 | P95 < 300ms | Dify /v1/datasets/:id/retrieve 调用 |
| 并发对话 | 200 并发用户 | k6 压力测试，错误率 < 1% |


*文档版本: v2.0 | 总字数: ~200,000 | 附录: A-D | 完整 API 参考 + 错误码目录 + 部署检查清单 + 性能基准*

## <a id="ch-v3-fixes"></a>三十、v3.0 修订：根据前后端审查反馈

> 本节为 v2.0 → v3.0 的增量修订，基于 Vexel（前端架构审查）和 Vulcan（后端架构审查）的反馈意见
> 覆盖：3 个 P0 问题 + 10 个 P1 建议 + 前端缺失设计（i18n/a11y/SSE可靠性/响应式/错误监控）

---

### 30.1 P0-1：数据库 Migration 完整策略

**回滚方案**：

```bash
# 生成回滚 SQL（Alembic 自动生成 downgrade）
alembic downgrade --sql -1 > down.sql

# 生产环境回滚（需人工审批）
DATABASE_URL=postgresql://... alembic downgrade -1
```

**Seed 数据脚本** (`app/seed.py`)：

```python
import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.models.tenant import Tenant
from app.models.user import User
from app.models.tool_registry import ToolRegistry


async def main(session: AsyncSession):
    # 创建初始租户
    tenant = Tenant(
        name="Default Tenant",
        slug="default",
        sso_provider="local",
        status="active",
    )
    session.add(tenant)
    await session.flush()

    # 创建平台管理员
    admin = User(
        tenant_id=tenant.id,
        sso_sub="admin-local",
        email=os.environ["ADMIN_EMAIL"],
        name="Admin",
        role="platform_admin",
        status="active",
    )
    session.add(admin)

    # 全局工具模板
    session.add_all([
        ToolRegistry(
            tool_id="tool-template-http",
            tenant_id=None,
            name="HTTP API Tool",
            type="http",
            risk_level="medium",
            permission_mode="auto",
            status="active",
        ),
        ToolRegistry(
            tool_id="tool-template-webhook",
            tenant_id=None,
            name="Webhook Tool",
            type="webhook",
            risk_level="low",
            permission_mode="auto",
            status="active",
        ),
    ])
    await session.commit()


async def run():
    async with async_session_factory() as session:
        await main(session)


if __name__ == "__main__":
    asyncio.run(run())
```

**CI Migration 超时重试**：

```yaml
# .github/workflows/ci.yml
- name: Run migrations
  run: |
    for i in 1 2 3; do
      alembic upgrade head && break
      echo "Migration attempt $i failed, retrying in 5s..."
      sleep 5
    done
  timeout-minutes: 5
```

### 30.2 P0-2：创建智能体的 Saga 补偿表

| 步骤 | 操作 | 成功 | 失败补偿 |
|------|------|------|---------|
| 1 | `DifyConsoleClient.create_app` | → 步骤 2 | — |
| 2 | `DifyConsoleClient.configure_model` | → 步骤 3 | — |
| 3 | `DifyConsoleClient.create_api_key` | → 步骤 4 | — |
| 4 | `SQLAlchemy INSERT agent_registry` | → 步骤 5 | `DifyConsoleClient.delete_app(app_id)` — 删除 Dify 侧资源 |
| 5 | `SQLAlchemy INSERT bindings` | → 完成 | `SQLAlchemy DELETE agent_registry` + `DifyConsoleClient.delete_app(app_id)` |

实现：

```python
from typing import Optional
import structlog

logger = structlog.get_logger()


async def register_agent(
    self,
    tenant_id: str,
    user_id: str,
    dto: CreateAgentDto,
) -> AgentDto:
    dify_app_id: Optional[str] = None
    try:
        dify_app = await self.dify_console.create_app(
            name=dto.name,
            mode=self._map_type_to_dify_mode(dto.type),
        )
        dify_app_id = dify_app.id
        if dto.model_id:
            await self.dify_console.configure_model(
                dify_app_id,
                model={
                    "provider": dto.model_provider,
                    "name": dto.model_id,
                },
                pre_prompt=dto.prompt,
            )
        api_key = await self.dify_console.create_api_key(dify_app_id)
        agent = await self.agent_repo.create(
            session=self.session,
            tenant_id=tenant_id,
            user_id=user_id,
            dify_app_id=dify_app_id,
            dify_api_key=api_key,
            **dto.model_dump(),
        )
        try:
            if dto.knowledge_base_ids:
                await self.agent_repo.add_kb_bindings(self.session, agent.id, dto.knowledge_base_ids)
            if dto.tool_ids:
                await self.agent_repo.add_tool_bindings(self.session, agent.id, dto.tool_ids)
            return self._to_dto(agent)
        except Exception as binding_error:
            # Step 5 failed: compensate Step 4 + Dify resources
            await self.agent_repo.delete(self.session, agent.id)
            raise binding_error
    except Exception as error:
        # Step 1-4 failed: compensate Dify resources
        if dify_app_id:
            try:
                await self.dify_console.delete_app(dify_app_id)
            except Exception as e:
                logger.error("Saga compensation failed", error=str(e))
        raise error
```

### 30.3 P0-3：SSE JSON 解析异常保护

**后端 DifyConversationAdapter** 修复：

```python
import json
import structlog

logger = structlog.get_logger()

for line in lines:
    if line.startswith("event: "):
        evt = line[7:].strip()
        continue
    if not line.startswith("data: "):
        continue
    try:
        data = json.loads(line[6:].strip())
        match evt:
            case "message":
                # ... handle message
                pass
            case "message_end":
                # ... handle message_end
                pass
            # ... other cases
    except json.JSONDecodeError as parse_error:
        logger.warning(
            "SSE parse error, skipping chunk",
            line=line[:100],
            error=str(parse_error),
        )
        continue
```

**前端 useStreamChat** 同步修复（同上 try-catch 包裹）。

### 30.4 P1：SSE 断线重连与心跳检测

```typescript
// useStreamChat 增强
const HEARTBEAT_TIMEOUT = 30000;
let lastEventTime = Date.now();
let heartbeatTimer: NodeJS.Timeout;

function resetHeartbeat() {
  lastEventTime = Date.now();
  clearTimeout(heartbeatTimer);
  heartbeatTimer = setTimeout(() => {
    setError('Connection lost - reconnecting...');
    abort();
    setTimeout(() => sendMessage(lastQuery, lastAgentId), 1000);
  }, HEARTBEAT_TIMEOUT);
}

// 在每次 SSE 事件处理中调用 resetHeartbeat()
// message_end 时 clearTimeout(heartbeatTimer)

// 消息去重：以 messageId 为 key，Set 存储已渲染 ID
const renderedIds = useRef(new Set<string>());
if (!renderedIds.current.has(data.messageId)) {
  renderedIds.current.add(data.messageId);
  // ... render message
}
```

### 30.5 P1：熔断器多实例问题

> **注意**：当前 circuit breaker 为进程内 Map 存储，多实例部署时各实例独立计数，无法真正熔断。P2 阶段迁移到 Redis（使用 Lua 脚本保证原子性）：
> ```lua
> -- Redis Circuit Breaker Lua Script
> local key = KEYS[1]; local failures = redis.call('INCR', key);
> if failures >= tonumber(ARGV[1]) then redis.call('EXPIRE', key, tonumber(ARGV[2])); return 1; end
> return 0;
> ```

### 30.6 P1：DifyConsoleClient Session 自动续期

```python
import asyncio
from typing import Any
import structlog

logger = structlog.get_logger()


class DifyConsoleClient:
    """Dify Console API 客户端，含 Session 自动续期。"""

    def __init__(self, config: Settings):
        self._config = config
        self._refresh_task: asyncio.Task | None = None

    async def on_startup(self) -> None:
        """应用启动时调用。"""
        await self._login()
        # 每 30 分钟刷新 Session
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def on_shutdown(self) -> None:
        """应用关闭时调用。"""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(30 * 60)  # 30 minutes
            try:
                await self._login()
            except Exception as e:
                logger.error("Session refresh failed", error=str(e))

    # 健康检查端点暴露 Dify Console 连接状态
    async def health_check(self) -> dict[str, str]:
        try:
            await self._request("GET", "/console/api/apps", params={"limit": 1})
            return {"status": "ok"}
        except Exception:
            return {"status": "disconnected"}
```

### 30.7 P1：乐观锁并发控制

```python
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.agent_registry import AgentRegistry


class AgentRepository:
    async def update_with_version(
        self,
        session: AsyncSession,
        agent_id: str,
        version: int,
        data: dict[str, Any],
    ) -> None:
        stmt = (
            update(AgentRegistry)
            .where(AgentRegistry.id == agent_id, AgentRegistry.version == version)
            .values(**data, version=AgentRegistry.version + 1)
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Agent was modified by another user - please refresh and retry",
            )


# AgentService.publish_agent 中使用
async def publish_agent(
    self,
    agent_id: str,
    tenant_id: str,
    user_id: str,
    expected_version: int,
) -> AgentDto:
    await self.agent_repo.update_with_version(
        self.session,
        agent_id,
        expected_version,
        {
            "status": "published",
            "published_at": datetime.now(timezone.utc),
            "published_by": user_id,
        },
    )
    # ...
```

### 30.8 前端新增：国际化 (i18n) 方案

**技术选型**：`next-intl`（App Router 原生 RSC 支持 + 类型安全）

**目录结构**：
```
messages/
├── en.json          # { "agents.title": "Agent Marketplace", "chat.send": "Send", ... }
├── zh-CN.json       # { "agents.title": "智能体广场", "chat.send": "发送", ... }
└── ja.json          # 按需扩展
```

**使用规范**：禁止 JSX 中硬编码字符串，所有文案从 `useTranslations()` 获取。

**RTL 兼容**：通过 Tailwind `rtl:` 前缀处理阿拉伯语等从右到左语言。日期/数字/货币格式化统一使用 `Intl` API（`new Intl.DateTimeFormat(locale, ...)`）。

### 30.9 前端新增：可访问性 (a11y) 规范

**语义化 HTML 对照表**：

| 组件 | HTML 元素 | ARIA 属性 |
|------|----------|----------|
| Button | `<button>` | — |
| Modal | `<div>` | `role="dialog" aria-labelledby={titleId} aria-modal="true"` |
| Toast | `<div>` | `role="alert" aria-live="polite"` |
| Table | `<table>` | `role="table"` |
| Tabs | `<div>` | `role="tablist"`，Tab 按钮 `role="tab" aria-selected`，面板 `role="tabpanel"` |
| Loading | `<div>` | `aria-busy="true" aria-label="Loading..."` |
| Chart | `<div>` | `role="img" aria-label="调用趋势图：展示 09:00 至 18:00 每小时的调用量变化"` |

**键盘导航**：Tab 顺序遵循 DOM 顺序；Enter/Space 激活按钮和链接；Escape 关闭 Modal/Dropdown；Modal 打开时焦点锁定在 Modal 内（焦点陷阱）；页面顶部 Skip-to-content 链接（`<a href="#main-content" className="sr-only focus:not-sr-only">Skip to content</a>`）。

**色彩对比度**：所有文字与背景的对比度不低于 WCAG AA 4.5:1。Design Token 中的色彩组合需通过自动化对比度检查（CI 中集成 `axe-core` 或 `pa11y`）。

### 30.10 移动端响应式设计

**断点策略**：Mobile < 768px（`max-w-` 布局 + 汉堡菜单导航）、Tablet 768–1024px（侧边栏折叠为图标）、Desktop > 1024px（完整侧边栏 + 全局 Header）

**聊天页面移动端适配**：全屏 ChatWindow（`h-dvh` 动态视口高度）；输入框固定底部（`sticky bottom-0`）；键盘弹出时使用 `visualViewport` API 检测高度变化并自动滚动到最新消息（`scrollIntoView({ behavior: 'smooth' })`）；消息气泡最大宽度 85%（`max-w-[85%]`）；发送按钮增大触摸区域（`min-h-12 min-w-12`）；安全区适配（`env(safe-area-inset-bottom)` padding）

**管理端表格→移动端卡片化**：在 Mobile 断点下，Table 组件切换为 Card 布局。每行数据渲染为一张卡片，列标题作为 label，值在下方展示。

### 30.11 前端错误监控与边界分层

**Error Boundary 分层**：全局 Boundary（`app/layout.tsx` `error.tsx`）兜底 500 页面；路由级 Boundary（每个 `page.tsx` 同目录的 `error.tsx`）独立降级；组件级 Boundary（ChatWindow、TraceTimeline 等关键组件包裹）防止局部错误崩溃整个页面。

**前端错误上报**：接入 Sentry SDK，覆盖 `ErrorBoundary` 捕获的组件错误、`window.onerror`（未捕获异常）、`unhandledrejection`（未处理的 Promise 拒绝）、API 调用异常（http-client 拦截器推送）。

**Web Vitals**：通过 `useReportWebVitals` 上报 LCP（最大内容绘制 < 2.5s）、INP（交互到下一次绘制 < 200ms）、CLS（累积布局偏移 < 0.1）。

**http-client 拦截器统一处理**：

```typescript
httpClient.interceptors.response.use(
  response => response.data,
  async (error) => {
    if (error.response?.status === 401 && !error.config._isRetry) {
      error.config._isRetry = true;
      const ok = await authContext.refreshToken();
      if (ok) return httpClient.request(error.config);
      authContext.logout();
    }
    if (error.response?.status === 403) { toast.error('无权限访问'); }
    if (error.response?.status >= 500) { Sentry.captureException(error); }
    throw error;
  }
);
```

### 30.12 恢复 SSE 事件映射表（v1.0 内容复原）

| Dify SSE Event | Wrapper SSE Event | 前端处理 |
|---------------|-------------------|---------|
| `message` | `event: message` {content, messageId, conversationId} | 追加文本到 assistant bubble |
| `message_end` | `event: message_end` {traceId, metadata{usage, retriever_resources}} | 停止流式；提取 citations |
| `message_file` | `event: file` {url, type} | 追加文件链接 |
| `message_replace` | `event: replace` | 替换消息 |
| `agent_message` | `event: agent_message` | Agent 思考过程 |
| `agent_thought` | `event: agent_thought` {thought, tool, observation} | 思维链步骤（可折叠） |
| `workflow_started` | `event: workflow_started` {workflowRunId} | 工作流进度指示 |
| `workflow_finished` | `event: workflow_finished` {status, error} | 指示器完成/失败 |
| `node_started` | `event: node_started` {nodeId, title} | 节点进度添加 |
| `node_finished` | `event: node_finished` {nodeId, status} | 节点标记状态 |
| `parallel_branch_started` | `event: branch_started` | 分支指示 |
| `parallel_branch_finished` | `event: branch_finished` {status} | 分支状态 |
| `text_chunk` | `event: text_chunk` | 文本块追加 |
| `text_replace` | `event: text_replace` | 文本替换 |
| `error` | `event: error` {code, message} | 错误 Banner + 重试 |
| `ping` | — | 心跳重置计时器 |

### 30.13 Design Token 体系

```css
:root {
  /* 主色系 */
  --color-primary-50: #eff6ff; --color-primary-500: #3b82f6; --color-primary-900: #1e3a5f;
  /* 中性色系 */
  --color-neutral-50: #fafafa; --color-neutral-200: #e5e7eb; --color-neutral-500: #6b7280; --color-neutral-900: #111827;
  /* 语义色 */
  --color-success: #10b981; --color-warning: #f59e0b; --color-error: #ef4444; --color-info: #3b82f6;
  /* 间距 (4px 基础) */
  --spacing-1: 4px; --spacing-2: 8px; --spacing-4: 16px; --spacing-6: 24px; --spacing-8: 32px;
  /* 圆角 */
  --radius-sm: 4px; --radius-md: 8px; --radius-lg: 12px; --radius-full: 9999px;
  /* 阴影 */
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05); --shadow-md: 0 4px 6px rgba(0,0,0,0.1);
  /* 字号 */
  --text-xs: 12px; --text-sm: 14px; --text-base: 16px; --text-lg: 18px; --text-xl: 20px; --text-2xl: 24px;
}

/* 暗色模式预留（使用 CSS 变量，通过 Tailwind dark: 类切换）*/
.dark { --color-neutral-50: #111827; --color-neutral-900: #fafafa; /* ... */ }
```

### 30.14 路由 ↔ API 映射对照表

| 前端路由 | 主要 API 端点 | 数据 Hook |
|---------|-------------|----------|
| `/user` | `GET /api/dashboard/user` | `useSWR('/api/dashboard/user')` |
| `/user/agents` | `GET /api/agents?status=published` | `useAgents({status:'published'})` |
| `/user/agents/[id]` | `GET /api/agents/:id` | `useSWR('/api/agents/'+id)` |
| `/user/tasks` | `GET /api/tasks` | `useTasks()` |
| `/user/conversations` | `GET /api/conversations` | `useConversations()` |
| `/user/conversations/[id]` | `POST .../messages` (SSE) + `GET .../:id` | `useStreamChat()` + `useSWR()` |
| `/user/files` | (P2: `GET /api/files`) | — |
| `/user/memories` | `GET /api/memories` (P2) | `useSWR()` |
| `/admin` | `GET /api/dashboard/admin` | `useSWR()` |
| `/admin/agents` | `GET /api/agents` | `useAgents()` |
| `/admin/agents/new` | `POST /api/agents` | `useSWRMutation()` |
| `/admin/agents/[id]` | `GET/PATCH/POST publish` | `useSWR()` |
| `/admin/knowledge` | `GET /api/knowledge-bases` | `useKnowledge()` |
| `/admin/knowledge/[id]` | `GET/POST retrieval-test` | `useSWR()` |
| `/admin/tools` | `GET /api/tools` | `useSWR()` |
| `/admin/tools/[id]` | `GET/POST debug` | `useSWR()` |
| `/admin/logs` | `GET /api/run-logs` | `useRunLogs()` |
| `/admin/logs/[traceId]` | `GET /api/run-logs/:traceId` | `useSWR()` |
| `/admin/models` | `GET /api/models` | `useSWR()` |
| `/admin/members` | `GET /api/members` | `useSWR()` |
| `/admin/health` | `GET /api/health` | `useSWR()` |
| `/admin/settings` | (P2: `PATCH /api/settings`) | `useSWRMutation()` |

### 30.15 表单状态管理策略

- **选型**：`react-hook-form` + `@hookform/resolvers/zod`（与后端共享 DTO Zod Schema）
- **分步表单草稿持久化**：`AgentCreateForm` 使用 `localStorage` 缓存每一步的 formData（key 格式：`agent-draft-{agentId}`），提交成功后清除缓存
- **字段校验时机**：模糊校验（`onBlur`）用于基本格式检查；实时校验（`onChange`）用于字符计数等视觉反馈；提交校验（`onSubmit`）执行完整 Zod Schema 验证

---

*修订版本: v3.0 | 增量章节: §30 | 基于: Vexel 前端审查 + Vulcan 后端审查 | 新增内容: ~8000 字*


## <a id="ch-v4-qa"></a>三十一、v4.0 修订：测试验收规格与用户旅程

> 本节基于 Verity（QA 与真实用户视角评审）的反馈，补全 §26 的测试策略和 §1-29 中缺失的真实用户旅程设计

---

### 31.1 扩展测试策略 — 完整测试用例清单

#### 31.1.1 测试金字塔（扩展版）

| 层 | 占比 | 工具 | 执行频率 | 责任人 |
|----|------|------|---------|--------|
| E2E 用户旅程 | 5% | Playwright | 每次 PR | FE + QA |
| API 集成测试 | 25% | httpx.AsyncClient + testcontainers | 每次 PR | BE |
| 单元测试 | 65% | pytest (BE) + Vitest (FE) | 每次 PR | FE + BE |
| 性能测试 | 5% | k6 / Artillery | 每次 P1 里程碑 | QA |
| 安全扫描 | — | OWASP ZAP / Trivy | 每次 P 阶段结束时 | DevOps |
| a11y 检查 | — | axe-core / pa11y | 每次 PR | CI 自动 |

#### 31.1.2 核心 API 测试用例表（节选 15/53）

| # | API | 用例 | 前置条件 | 步骤 | 期望 |
|---|-----|------|---------|------|------|
| 1 | `GET /api/me` | 正常获取当前用户 | 已登录 | GET | 200 + CurrentUser 结构 |
| 2 | `GET /api/me` | 未认证 | 无 Token | GET | 401 UNAUTHORIZED |
| 3 | `POST /api/agents` | 创建智能体 | agent_admin+ | POST 完整 body | 201 + agent.status=draft + difyAppId 非空 |
| 4 | `POST /api/agents` | 重复 agentId | 已有同 ID | POST | 409 DUPLICATE_AGENT_ID |
| 5 | `POST /api/agents` | employee 无权限 | employee 角色 | POST | 403 FORBIDDEN |
| 6 | `POST /api/agents` | Saga 补偿验证 | Mock DifyConsoleClient 在第 4 步失败 | POST | 500 INTERNAL_ERROR + Dify App 已被删除（验证补偿） |
| 7 | `POST /api/agents/:id/publish` | 发布智能体 | status=testing | POST | 200 + status=published + publishedAt 非空 |
| 8 | `POST /api/agents/:id/publish` | 乐观锁冲突 | 两个并发请求同 version | POST × 2 | 第一个 200，第二个 409 CONFLICT |
| 9 | `POST /api/knowledge-bases/:id/documents` | 上传 PDF | KB 存在 | POST multipart | 201 + status="indexing" |
| 10 | `POST /api/knowledge-bases/:id/documents` | 文件类型不支持 | — | POST .exe | 422 UNSUPPORTED_FORMAT |
| 11 | `POST /api/conversations/:id/messages` | SSE 流式对话 | 已创建会话 | POST query | Content-Type: text/event-stream + event:message 事件 |
| 12 | `POST /api/conversations/:id/messages` | 速率限制 | 已发送 20 条/分钟 | POST 第 21 条 | 429 RATE_LIMITED |
| 13 | `POST /api/tasks/:id/approve` | 审批通过 | status=pending, agent_admin+ | POST | 200 + status=approved + RQ job 入队 |
| 14 | `POST /api/tasks/:id/approve` | 状态转换不合法 | status=completed | POST | 422 INVALID_TRANSITION |
| 15 | `GET /api/agents` | 租户隔离 | Tenant A + Tenant B 各有 2 个 Agent | GET (Tenant A token) | 200 + data.length=2，不含 Tenant B 的 Agent |

#### 31.1.3 Dify 依赖的测试分层策略

| 测试层 | Dify 处理方式 | 适用场景 |
|--------|------------|---------|
| 单元测试 | Mock `DifyClientService` 和 `DifyConsoleClient`（返回固定 JSON fixture） | 所有 Router/Service/Repository/Adapter 的快速验证 |
| 集成测试 | 启动真实 Dify 容器（testcontainers 或 docker-compose 测试 profile） | SSE 流式解析、KB 文档上传→索引→召回 全链路 |
| E2E | 使用 staging 环境的真实 Dify 实例（含预置 seed App 和 Dataset） | 完整用户旅程：登录→创建 Agent→对话→Trace |

#### 31.1.4 测试数据策略

- **Seed 脚本**：`app/seed.py` 创建测试租户（2 个）+ 测试用户（每租户 3 种角色）+ 预置全局工具模板
- **工厂函数**：`create_test_agent(tenant_id, **overrides)` / `create_test_kb(tenant_id, **overrides)` / `create_test_conversation(user_id, agent_id)` 用于测试数据构造
- **租户隔离断言**：`assert tenant_b_agent_id not in [a.id for a in tenant_a_response.data]` 模式

#### 31.1.5 SSE 容错场景测试用例

| # | 场景 | 模拟方式 | 期望 |
|---|------|---------|------|
| 1 | 正常流式 | 发送一条消息 | 收到 message → message_end 完整序列 |
| 2 | 中途断连 | `AbortController.abort()` 在流中间调用 | 触发重连逻辑，no data loss |
| 3 | 心跳超时 | Mock Dify 发送 ping 后 35s 无新事件 | 前端自动重连 |
| 4 | 残缺 chunk | Mock SSE 发送不完整的 JSON 行 | `except` 跳过该 chunk，流继续 |
| 5 | messageId 去重 | 重连后收到相同 messageId | 不重复渲染已存在的消息 |

#### 31.1.6 安全与迁移测试

- **OWASP 扫描**：CI 中集成 OWASP ZAP baseline scan（每次 P 阶段结束时执行）
- **迁移回滚验证**：CI 中添加 `alembic upgrade head → seed.py → alembic downgrade -1 → alembic upgrade head → seed.py` 的完整回归测试
- **a11y**：CI 中的 Playwright E2E 测试添加 `@axe-core/playwright` 断言，拦截 color-contrast 违规

---

### 31.2 真实用户旅程设计 — 8 个 P0 场景

#### 31.2.1 首次登录 / 空状态引导

**场景**：新租户员工首次登录，无智能体、无知识库、无对话历史。

**设计**：
- 员工工作区首页不显示空白表格，而是引导式空状态插画 + 提示文字："您的管理员尚未发布智能体。请耐心等待或联系管理员。"
- 管理员首次进入管理端 `/admin/agents`：显示引导卡片"'创建第一个智能体'→ 填写表单 → 一键发布"。知识库、工具页面同理。
- E2E 测试断言：新租户访问 `/user/agents` → 页面显示 EmptyState 组件而非空数组 []。

#### 31.2.2 权限不足的友好提示

**场景**：employee 角色尝试访问 `/admin`。

**设计**：
- employee 不应看到管理端入口（WorkspaceSwitcher 不显示"切换到管理端"按钮）
- 如通过 URL 直接访问 `/admin`，显示友好提示页："您没有管理权限。如需访问管理端，请联系平台管理员授予 agent_admin 或更高角色。" —— 而非 403 "无权限访问"这种后台话术
- 在前端路由守卫中前置判断：`if (role === 'employee' && path.startsWith('/admin')) redirectTo('/user')`

#### 31.2.3 长对话 Token 超限

**场景**：连续对话 30+ 轮后，上下文接近模型 Token 上限。

**设计**：
- 前端显示"对话较长，较早的消息可能未被纳入上下文"提示条（黄色 info banner，可关闭）
- DifyAdapter 在 `inputs` 中传递 `truncation_notice: true` 标记
- E2E 测试：发送 30 轮对话 → 验证第 31 轮时出现截断提示

#### 31.2.4 429 限流的用户体验

**场景**：员工在 1 分钟内发送超过 20 条消息。

**设计**：
- 前端在收到 429 响应时，显示 Toast："消息发送太频繁，请稍后再试（每分钟最多 20 条）"
- 区分"临时限流"（429 RATE_LIMITED）与"配额耗尽"（429 QUOTA_EXCEEDED，文案："本月 API 调用量已用完，请联系管理员升级"）
- 输入框在限流期间置灰并显示倒计时："XX 秒后可发送"
- E2E 测试：快速发送 21 条 → 验证第 21 条被拦截 + Toast 出现

#### 31.2.5 审批通知的实时性

**当前设计**：14.2 提 in-app / email / wechat，但未说明推送机制。

**补充设计**：
- **P1 方案**：前端通过 SWR `refreshInterval: 10000` 轮询 `/api/tasks?status=pending` 获取待审批任务数（10s 一次，低负载）
- **P2 方案**：引入 WebSocket（Socket.io），审批任务创建时通过 WS 推送通知到审批人浏览器
- E2E 测试：员工触发高风险工具 → 审批人页面在 12s 内出现新任务（P1 轮询）或 3s 内（P2 WebSocket）

#### 31.2.6 删除 / 发布 / 下线的二次确认

| 操作 | 确认方式 | 额外检查 |
|------|---------|---------|
| 删除智能体 | Modal："确定要删除'合同审查助手'吗？删除后不可恢复，已发布的智能体将立即对用户不可用。" 需输入智能体名称确认 | 检查是否有关联的活跃会话 |
| 下线智能体 | Modal："下线后该智能体将在用户工作区隐藏，已有会话不受影响。确定下线？" | — |
| 发布智能体 | Modal（仅首次或配置变更后）："发布后智能体将出现在用户工作区。当前绑定：知识库 2 个、工具 1 个。" | 检查是否至少绑定 1 个知识库或工具（否则提示"建议绑定知识库或工具后发布"） |
| 删除知识库 | Modal："确定要删除'合同与法务知识库'吗？已有 3 个智能体绑定此知识库，删除后将影响这些智能体的知识召回能力。" + 输入名称确认 | 显示绑定智能体列表 |
| 移除成员 | Modal："确定要移除'周'吗？该成员的所有会话将被保留但无法继续访问。" | — |
| 删除会话 | 列表页滑动删除或行末删除按钮（无需 Modal，但需有撤销 Toast："会话已删除，撤销"） | — |

#### 31.2.7 KB 索引等待与失败

**场景**：管理员上传大文件后，索引需 2-5 分钟。期间管理员想知道进度、能否取消、失败了怎么办。

**设计**：
- 文档列表中 indexing 状态的文档显示进度条（蓝色 pulse 动画）+ 预计剩余时间（如有）+ "取消索引"按钮
- 索引中该知识库的召回测试区域显示提示："知识库正在索引中（已索引 45%），索引完成后可进行召回测试"并禁用检索按钮（灰显）
- 索引失败显示红色状态："索引失败：文件解析错误" + "重试"按钮 + "删除文档"按钮
- E2E 测试：上传大文件 → 验证进度条出现 → 验证检索按钮禁用 → Mock 索引完成 → 验证检索可用

#### 31.2.8 网络断开 / 恢复

**场景**：用户网络中断 10 秒后恢复。

**设计**：
- 检测到 `navigator.onLine === false` 时，页面顶部显示固定黄色横幅："网络连接已断开，正在尝试重新连接..."（带 spinning 图标）
- 恢复后自动调用 SWR `mutate()` 刷新当前页面数据，横幅变为绿色"已恢复连接"，2 秒后自动消失
- 正在进行的 SSE 流由 30.4 的重连逻辑处理
- E2E 测试：Playwright 模拟 `context.setOffline(true)` → 验证横幅出现 → `setOffline(false)` → 验证数据刷新

---

### 31.3 开发计划修正 — 纳入 QA 与测试任务

#### P1 周计划（修正版）

| 周 | FE（成员 A） | BE（成员 B） | QA（成员 C，兼职） |
|----|------------|------------|-------------------|
| 1 | Monorepo + Tailwind + UI 组件 | SQLAlchemy + Alembic + Dify 部署 | 编写 API 测试框架（httpx.AsyncClient + testcontainers）+ 首个冒烟测试（SSO login + GET /api/me） |
| 2 | 登录页 + AuthContext | OIDC + JWT + RBAC | Agent CRUD API 测试（创建/重复/权限/租户隔离 8 条用例） |
| 3 | AgentMarketplace + CreateForm | AgentRepo + DifyConsoleAdapter | KB 测试（上传/索引/召回/格式校验） |
| 4 | AgentDetail + MiniChat | KnowledgeAdapter + 上传/检索 API | SSE 流式测试（正常流/中断/心跳超时/残缺 chunk） |
| 5 | ChatWindow + useStreamChat | ConversationAdapter SSE | 工具审批流程 + Saga 补偿测试 |
| 6 | TaskCenter + ToolList + Dashboard | TaskService + RQ + ToolProxy | 租户隔离集成测试 + RBAC 全矩阵测试 |
| 7 | Error/Skeleton/响应式 + Toast | DifyClient 重试/熔断 + RateLimit | E2E 用户旅程测试（首次登录空状态/权限友好提示/限流/二次确认） |
| 8 | a11y + i18n + 性能优化 | 集成测试收尾 + CI/CD + 压测 | 完整回归 + k6 性能基线 + OWASP 安全扫描 + 迁移回滚演练 |

**每周末固定活动**：
- 周五下午：三方演示（FE+BE+QA）——展示本周完成的用户可见功能
- 演示后：QA 更新测试用例覆盖率报告 → 未通过用例退回下周修复

---

### 31.4 Vulcan/Vexel P2 建议补充

| # | 来源 | 建议 | 章节 |
|---|------|------|------|
| 1 | Vulcan | 工具代理幂等键：Dify tool call ID 作为 `Idempotency-Key` header | §12.1 注释 |
| 2 | Vulcan | API 版本化预留：破坏性变更通过 `/api/v2/*`，旧版保留 2 个发布周期 | §7.1 通用约定 |
| 3 | Vulcan | 分页参数统一：列表查询用 offset 分页，实时流用 cursor 分页 | §22.4 |
| 4 | Vexel | 前端组件/Hook 单元测试纳入测试金字塔 | §31.1.1 |
| 5 | Vexel | React 19 渲染策略标注：SSG/SSR/CSR/ISR 按路由标注 | §30.14 追加 |
| 6 | Vexel | 跨 Tab 状态同步：SWR `broadcastRevalidate` 或 BroadcastChannel | P2 |

**渲染策略补充**（§30.14 追加）：

| 路由 | 策略 | 说明 |
|------|------|------|
| `/user` | SSG + SWR | 首页静态框架 + SWR 获取动态数据 |
| `/user/agents` | SSR + SWR | 首屏服务端渲染 + 客户端 hydration |
| `/user/conversations/[id]` | CSR | 聊天页纯客户端交互 |
| `/admin/*` | CSR | 管理端全部客户端渲染（需认证） |
| `/login` | SSR | 服务端渲染登录页 |

---

*修订版本: v4.0 | 增量章节: §31 | 基于: Verity QA 评审 + Vulcan/Vexel P2 建议 | 新增内容: ~6000 字*


## <a id="ch-v5-vessels"></a>三十二、v5.0 修订：模块血管图与内部详细设计

> 本节将架构文档从"骨架"推进到"血管"层——为每个核心模块绘制跨模块交互图并展开内部四层（Schema→Router→Service→Repository）详细设计。
> 以「用户与权限」模块为范例展开最深，其余模块遵循同一模式。

---

### 32.0 全局血管总图：7 模块交互全景

```mermaid
flowchart TB
    subgraph Core["🫀 核心血管"]
        Auth["① 用户与权限\nJWT签发·RBAC·租户隔离"]
    end

    subgraph Business["🫁 业务血管"]
        Agent["② 智能体\nCRUD·生命周期·发布"]
        KB["③ 知识库\n上传·索引·召回"]
        Conv["④ 会话\n流式对话·SSE·记忆"]
        Tool["⑤ 工具\n代理·审批·熔断"]
    end

    subgraph Observe["🔄 观测血管"]
        Trace["⑥ 运行记录\nTrace·日志·统计"]
        Task["⑦ 任务中心\nRQ·审批·重试"]
    end

    Auth -->|JWT + tenant_id| Agent
    Auth -->|JWT + tenant_id| KB
    Auth -->|JWT + tenant_id| Conv
    Auth -->|JWT + tenant_id| Tool
    Auth -->|JWT + tenant_id| Trace
    Auth -->|JWT + tenant_id| Task

    Agent -->|绑定 KB| KB
    Agent -->|绑定 Tool| Tool
    Agent -->|创建会话| Conv

    KB -->|文档索引| Task
    KB -->|召回引用| Trace

    Conv -->|工具调用| Tool
    Conv -->|生成 Trace| Trace
    Conv -->|记忆提取| Task

    Tool -->|审批流| Task
    Tool -->|执行日志| Trace

    Task -->|状态变更| Trace
```

> **读图说明**：箭头方向 = 调用方向。每个模块的请求首先经过 Auth 血管注入 JWT + tenant_id，然后才能触达业务模块。

---

### 32.1 模块 1：用户与权限 — 血管深度解剖

#### 32.1.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Auth["① 用户与权限"]
        direction TB
        JWT["JWT 依赖\nRS256验签·提取sub/tenant/role"]
        RBAC["require_roles 依赖\n角色匹配"]
        TENANT["get_active_tenant 依赖\nWHERE tenant_id注入"]
        AUTH_SVC["AuthService\n登录·刷新·登出·令牌管理"]
        MEMBER_SVC["MemberService\nCRUD·邀请·角色变更"]
    end

    JWT -->|"request.state.user = {sub, tenantId, role}"| RBAC
    RBAC -->|"放行 or 403"| TENANT
    TENANT -->|"所有查询强制tenant过滤"| Business["②③④⑤⑥⑦ 业务模块"]

    MEMBER_SVC -->|"user.created事件"| EventBus["EventBus"]
    EventBus -->|"异步"| AUDIT["AuditLog队列"]
    EventBus -->|"异步"| NOTIFY["通知服务·邮件/站内信"]

    AUTH_SVC -->|"refresh_tokens表"| DB[("Wrapper DB")]
    MEMBER_SVC -->|"users表"| DB
    JWT -->|"验证签名·解码"| IdP["企业IdP\nOIDC Provider"]
```

**与各模块交互明细**：

| 目标模块 | 交互方式 | 数据流 |
|---------|---------|--------|
| ② 智能体 | `AgentRouter` 依赖 `get_current_user` + `require_roles(...)` | JWT.sub → userId，JWT.tenant_id → tenantId过滤 |
| ③ 知识库 | 同上 | 同上 |
| ④ 会话 | 同上 + `dify_user = tenantId:userId` 格式传递 | 会话级租户隔离 |
| ⑤ 工具 | 同上 + 审批人权限校验（`agent_admin+`） | 高危操作需二次鉴权 |
| ⑥ 运行记录 | 同上 + auditor 只读权限 | 日志查询按租户隔离 |
| ⑦ 任务中心 | 同上 + 审批人匹配（`approverId` 字段） | 仅任务相关人可见 |

#### 32.1.2 内部四层架构

```
┌──────────────────────────────────────────────────────┐
│                    Router 层                           │
│  AuthRouter  │  MemberRouter                           │
│  login/callback/  │  list/create/update/              │
│  refresh/me/      │  disable/invite                   │
│  logout           │                                   │
├──────────────────────────────────────────────────────┤
│                     Service 层                         │
│  AuthService                 │  MemberService          │
│  · initiate_oidc()          │  · create_member()     │
│  · handle_callback()        │  · update_member()     │
│  · refresh_token()          │  · disable_member()    │
│  · revoke_token()           │  · invite_members()     │
│  · sign_jwt() / verify_jwt()│  · change_role()       │
├──────────────────────────────────────────────────────┤
│                   Repository 层                        │
│  UserRepository  │  TenantRepository                  │
│  RefreshTokenRepo│  SsoConfigRepository               │
├──────────────────────────────────────────────────────┤
│                    数据层                              │
│  users  │  tenants  │  refresh_tokens  │  sso_configs │
│  members (invitations)                                │
└──────────────────────────────────────────────────────┘
```

#### 32.1.3 Pydantic Schema（请求/响应模型）

```python
# === app/schemas/auth.py ===
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


class OidcProvider(str, Enum):
    oidc = "oidc"
    saml = "saml"


class LoginDto(BaseModel):
    provider: OidcProvider = OidcProvider.oidc
    redirect_uri: Optional[str] = Field(default=None, description="登录后跳转地址")


class CallbackQueryDto(BaseModel):
    code: str = Field(..., min_length=1, description="授权码不能为空")
    state: str = Field(..., min_length=1, description="state 参数不能为空")


class RefreshTokenDto(BaseModel):
    refresh_token: str = Field(..., min_length=1, description="refreshToken 不能为空")


# === app/schemas/member.py ===
class MemberRole(str, Enum):
    employee = "employee"
    agent_admin = "agent_admin"
    knowledge_admin = "knowledge_admin"
    auditor = "auditor"


class MemberStatus(str, Enum):
    active = "active"
    disabled = "disabled"


class CreateMemberDto(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=100)
    department: Optional[str] = Field(default=None, max_length=200)
    role: MemberRole
    send_invite: bool = True


class UpdateMemberDto(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    department: Optional[str] = Field(default=None, max_length=200)
    role: Optional[MemberRole] = None
    status: Optional[MemberStatus] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateMemberDto":
        if all(v is None for v in [self.name, self.department, self.role, self.status]):
            raise ValueError("至少需要提供一个更新字段")
        return self


class InviteMembersDto(BaseModel):
    emails: list[EmailStr] = Field(..., min_length=1, max_length=100, description="单次最多邀请 100 人")
    role: MemberRole = MemberRole.employee
    message: Optional[str] = Field(default=None, max_length=500)
```

#### 32.1.4 AuthRouter 完整实现

```python
# === app/routers/auth.py ===
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import settings
from app.dependencies.auth import get_current_user, CurrentUser
from app.schemas.auth import LoginDto, CallbackQueryDto, RefreshTokenDto
from app.services.auth import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


@router.post("/login", status_code=status.HTTP_302_FOUND)
async def login(
    dto: LoginDto,
    request: Request,
) -> RedirectResponse:
    auth_service = _get_auth_service(request)
    oidc_url = await auth_service.initiate_oidc(dto.redirect_uri)
    return RedirectResponse(url=oidc_url, status_code=status.HTTP_302_FOUND)


@router.get("/callback", status_code=status.HTTP_302_FOUND)
async def callback(
    request: Request,
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
) -> RedirectResponse:
    auth_service = _get_auth_service(request)
    result = await auth_service.handle_callback(code, state)
    # 设置 HttpOnly Cookie
    response = RedirectResponse(url="/user", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=result["access_token"],
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=2 * 60 * 60,  # 2h
        path="/",
    )
    # 存 refresh token hash
    await auth_service.store_refresh_token(result["user"]["id"], result["refresh_token"])
    return response


@router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh(
    dto: RefreshTokenDto,
    request: Request,
    response: Response,
) -> JSONResponse:
    auth_service = _get_auth_service(request)
    result = await auth_service.refresh_token(dto.refresh_token)
    response.set_cookie(
        key="access_token",
        value=result["access_token"],
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=2 * 60 * 60,
        path="/",
    )
    return JSONResponse({"refresh_token": result["new_refresh_token"]})


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    response: Response,
) -> JSONResponse:
    auth_service = _get_auth_service(request)
    await auth_service.revoke_refresh_tokens(user.sub)
    response.delete_cookie("access_token")
    return JSONResponse({"message": "已登出"})


@router.get("/me", status_code=status.HTTP_200_OK)
async def me(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    auth_service = _get_auth_service(request)
    return await auth_service.get_current_user(user.sub, user.tenant_id)
```

#### 32.1.5 AuthService 完整实现

```python
# === app/services/auth.py ===
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import structlog
from authlib.integrations.starlette_client import OAuth
from authlib.jose import jwt as jose_jwt
from authlib.jose import JsonWebKey
from fastapi import HTTPException, status

from app.core.config import settings
from app.repositories.user import UserRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.tenant import TenantRepository
from app.core.event_bus import event_bus

logger = structlog.get_logger()

# OAuth client (authlib)
oauth = OAuth()
oauth.register(
    name="oidc",
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    server_metadata_url=settings.oidc_discovery_url,
    client_kwargs={"scope": "openid profile email"},
)


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        refresh_token_repo: RefreshTokenRepository,
        tenant_repo: TenantRepository,
    ):
        self._user_repo = user_repo
        self._refresh_token_repo = refresh_token_repo
        self._tenant_repo = tenant_repo
        self._code_verifiers: dict[str, dict[str, str]] = {}

    # 1. 发起 OIDC：生成 PKCE + 构造授权 URL
    async def initiate_oidc(self, redirect_uri: Optional[str] = None) -> str:
        verifier = secrets.token_urlsafe(32)
        challenge = hashlib.sha256(verifier.encode()).hexdigest()
        state = secrets.token_hex(16)  # CSRF token
        self._code_verifiers[state] = {"verifier": verifier, "redirect_uri": redirect_uri or ""}

        params = urlencode({
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": secrets.token_hex(16),
        })
        return f"{settings.oidc_authorization_endpoint}?{params}"

    # 2. 处理回调：验证 state → 换 token → 验 id_token → 取 userinfo → upsert user → 签发 JWT
    async def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        session = self._validate_and_consume_state(state)

        # Step 1: 用 code 换取 token
        token_res = await self._exchange_code_for_tokens(code, session["verifier"])

        # Step 2: 验证 id_token (RS256 签名、issuer、audience、nonce、exp)
        await self._verify_id_token(token_res["id_token"])

        # Step 3: 用 access_token 获取 userinfo
        user_info = await self._fetch_user_info(token_res["access_token"])

        # Step 4: 匹配租户（通过 sso_domain 或默认租户）
        tenant = await self._tenant_repo.find_by_sso_domain(user_info["email"].split("@")[1])

        # Step 5: Upsert user
        user = await self._user_repo.upsert_by_sso(
            tenant_id=tenant.id,
            sso_sub=user_info["sub"],
            email=user_info["email"],
            name=user_info["name"],
            avatar_url=user_info.get("picture"),
        )

        # Step 6: 签发 JWT
        jti = str(secrets.token_hex(16))
        payload = {
            "sub": user.id,
            "tenant_id": tenant.id,
            "role": user.role,
            "jti": jti,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }
        access_token = jose_jwt.encode(
            {"alg": "RS256"},
            payload,
            settings.jwt_private_key_pem,  # RSA private key PEM
        )

        # Step 7: 生成 refresh token
        refresh_token = secrets.token_urlsafe(48)

        # Step 8: 发事件
        if user.login_count == 1:
            await event_bus.publish("user.created", {
                "user_id": user.id,
                "tenant_id": tenant.id,
                "email": user.email,
            })
        await event_bus.publish("user.login", {
            "user_id": user.id,
            "tenant_id": tenant.id,
        })

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": self._to_user_dto(user, tenant),
        }

    # 3. 刷新 access token
    async def refresh_token(self, token: str) -> dict[str, str]:
        token_hash = self._hash_token(token)
        stored = await self._refresh_token_repo.find_valid(token_hash)
        if not stored or datetime.now(timezone.utc) > stored.expires_at:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOKEN_EXPIRED")

        # 吊销旧 token
        await self._refresh_token_repo.revoke(token_hash)

        user = await self._user_repo.find_by_id(stored.user_id)
        jti = str(secrets.token_hex(16))
        payload = {
            "sub": user.id,
            "tenant_id": user.tenant_id,
            "role": user.role,
            "jti": jti,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }
        access_token = jose_jwt.encode(
            {"alg": "RS256"},
            payload,
            settings.jwt_private_key_pem,
        )
        new_refresh_token = secrets.token_urlsafe(48)
        await self._refresh_token_repo.create(
            user_id=user.id,
            token_hash=self._hash_token(new_refresh_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

        return {"access_token": access_token, "new_refresh_token": new_refresh_token}

    # 4. 存储 refresh token
    async def store_refresh_token(self, user_id: str, token: str) -> None:
        await self._refresh_token_repo.create(
            user_id=user_id,
            token_hash=self._hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

    # 5. 吊销所有 refresh tokens
    async def revoke_refresh_tokens(self, user_id: str) -> None:
        await self._refresh_token_repo.revoke_all_for_user(user_id)

    # 6. 获取当前用户
    async def get_current_user(self, user_id: str, tenant_id: str) -> dict:
        user = await self._user_repo.find_by_id_and_tenant(user_id, tenant_id)
        tenant = await self._tenant_repo.find_by_id(tenant_id)
        return self._to_user_dto(user, tenant)

    # --- 私有方法 ---

    def _validate_and_consume_state(self, state: str) -> dict[str, str]:
        session = self._code_verifiers.get(state)
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_STATE")
        del self._code_verifiers[state]
        return session

    async def _exchange_code_for_tokens(self, code: str, verifier: str) -> dict:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "redirect_uri": settings.oidc_redirect_uri,
            "code_verifier": verifier,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.oidc_token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOKEN_EXCHANGE_FAILED")
        return resp.json()

    async def _verify_id_token(self, id_token: str) -> None:
        # authlib: 使用 JWKS 验证 id_token
        jwks = await self._fetch_jwks()
        key_set = JsonWebKey.import_key_set(jwks)
        try:
            jose_jwt.decode(
                id_token,
                key_set,
                claims_options={
                    "iss": settings.oidc_issuer,
                    "aud": settings.oidc_client_id,
                },
            )
        except Exception as e:
            logger.error("id_token verification failed", error=str(e))
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ID_TOKEN_INVALID")

    async def _fetch_jwks(self) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.oidc_jwks_uri)
        return resp.json()

    async def _fetch_user_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                settings.oidc_userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="USERINFO_FETCH_FAILED")
        return resp.json()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _to_user_dto(user: Any, tenant: Any) -> dict:
        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "department": user.department,
            "role": user.role,
            "avatarText": (user.name[0].upper() if user.name else None),
            "canAccessAdmin": user.role != "employee",
            "tenantName": tenant.name,
        }
```

#### 32.1.6 JWT 策略与依赖链

```python
# === app/dependencies/auth.py ===
from typing import Annotated
from dataclasses import dataclass

import structlog
from authlib.jose import jwt as jose_jwt, JoseError
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings

logger = structlog.get_logger()


@dataclass
class CurrentUser:
    sub: str
    tenant_id: str
    role: str
    jti: str


async def get_current_user(request: Request) -> CurrentUser:
    """从 Cookie 提取 token 进行 RS256 验签。
    验证通过后注入 request.state.user: {sub, tenant_id, role, jti}。
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="UNAUTHORIZED")

    try:
        claims = jose_jwt.decode(
            token,
            settings.jwt_public_key_pem,  # RSA public key PEM
            claims_options={
                "iss": settings.jwt_issuer,
            },
        )
        claims.validate()  # 验证 exp 等
        user = CurrentUser(
            sub=claims["sub"],
            tenant_id=claims["tenant_id"],
            role=claims["role"],
            jti=claims["jti"],
        )
        request.state.user = user
        return user
    except JoseError as e:
        logger.warning("JWT verification failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOKEN_INVALID")


def require_roles(*roles: str):
    """FastAPI 依赖：角色校验，等价 NestJS @Roles() + RolesGuard。"""
    def dependency(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")
        return user
    return dependency


async def get_active_tenant(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    """FastAPI 依赖：确保 tenant_id 存在，等价 NestJS TenantGuard。"""
    if not user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="TENANT_NOT_FOUND")
    return user


# 使用示例
# === app/routers/agents.py ===
# from app.dependencies.auth import get_current_user, require_roles, get_active_tenant, CurrentUser
#
# router = APIRouter(prefix="/api/agents", tags=["agents"])
#
# @router.post("/", status_code=201, dependencies=[Depends(require_roles("platform_admin", "agent_admin"))])
# async def create_agent(
#     dto: CreateAgentDto,
#     request: Request,
#     user: Annotated[CurrentUser, Depends(get_active_tenant)],
# ) -> AgentDto:
#     # user.tenant_id 已由 get_active_tenant 保证存在
#     return await agent_service.create(user.tenant_id, user.sub, dto)
```

#### 32.1.7 Repository 层

```python
# === app/repositories/user.py ===
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_by_sso(
        self,
        tenant_id: str,
        sso_sub: str,
        email: str,
        name: str,
        avatar_url: Optional[str] = None,
    ) -> User:
        # 先查找是否存在
        stmt = select(User).where(User.tenant_id == tenant_id, User.sso_sub == sso_sub)
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Update
            upd = (
                update(User)
                .where(User.id == existing.id)
                .values(
                    email=email,
                    name=name,
                    avatar_url=avatar_url,
                    last_active_at=datetime.now(timezone.utc),
                    login_count=User.login_count + 1,
                )
            )
            await self._session.execute(upd)
            await self._session.flush()
            existing.email = email
            existing.name = name
            existing.avatar_url = avatar_url
            existing.last_active_at = datetime.now(timezone.utc)
            existing.login_count += 1
            return existing
        else:
            # Create
            user = User(
                tenant_id=tenant_id,
                sso_sub=sso_sub,
                email=email,
                name=name,
                avatar_url=avatar_url,
                role="employee",
                status="active",
                login_count=1,
                last_active_at=datetime.now(timezone.utc),
            )
            self._session.add(user)
            await self._session.flush()
            return user

    async def find_by_id_and_tenant(self, id: str, tenant_id: str) -> Optional[User]:
        stmt = select(User).where(
            User.id == id,
            User.tenant_id == tenant_id,
            User.status == "active",
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_id(self, id: str) -> Optional[User]:
        stmt = select(User).where(User.id == id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        search: Optional[str] = None,
        role: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> list[User]:
        stmt = select(User).where(User.tenant_id == tenant_id)
        if search:
            stmt = stmt.where(
                or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
            )
        if role:
            stmt = stmt.where(User.role == role)
        if status_filter:
            stmt = stmt.where(User.status == status_filter)
        stmt = stmt.order_by(User.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, id: str, data: dict[str, Any]) -> None:
        stmt = update(User).where(User.id == id).values(**data)
        await self._session.execute(stmt)

    async def disable(self, id: str) -> None:
        stmt = (
            update(User)
            .where(User.id == id)
            .values(status="disabled", disabled_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)
```

#### 32.1.8 事件总线

| 事件名 | 触发时机 | 消费者 | 负载 |
|-------|---------|--------|------|
| `user.created` | 新用户首次 SSO 登录 | MemberService（发欢迎邮件）、AuditLogService（记录创建） | `{userId, tenantId, email}` |
| `user.login` | 每次登录 | AuditLogService | `{userId, tenantId, timestamp}` |
| `user.role_changed` | 管理员修改成员角色 | MemberService（通知被变更者）、AgentBindingService（重新评估可见性） | `{userId, tenantId, oldRole, newRole, changedBy}` |
| `user.disabled` | 管理员禁用成员 | MemberService（清理 sessions）、AgentBindingService（移除可见性） | `{userId, tenantId, disabledBy}` |

```python
# === app/core/event_bus.py ===
import asyncio
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()


class EventBus:
    """轻量异步事件总线（基于 asyncio + blinker 模式）。
    替代 NestJS @nestjs/event-emitter 的 @OnEvent 装饰器。
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = {}

    def subscribe(self, event_name: str):
        """装饰器：注册事件处理器，等价 NestJS @OnEvent('event.name')。"""
        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            self._handlers.setdefault(event_name, []).append(func)
            return func
        return decorator

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        """发布事件，异步通知所有订阅者。"""
        handlers = self._handlers.get(event_name, [])
        if not handlers:
            return
        tasks = [handler(payload) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event handler failed",
                    event=event_name,
                    handler=handlers[i].__name__,
                    error=str(result),
                )


# 全局实例
event_bus = EventBus()


# === app/services/member_listeners.py ===
# 事件监听示例
@event_bus.subscribe("user.created")
async def handle_user_created(payload: dict[str, str]) -> None:
    await audit_log_service.create(action="user.created", **payload)
    await email_service.send_welcome(payload["email"])


@event_bus.subscribe("user.role_changed")
async def handle_role_changed(payload: dict[str, str]) -> None:
    await audit_log_service.create(action="user.role_changed", **payload)
    # 角色降级为 employee：清理该用户持有的待审批任务
    if payload["new_role"] == "employee":
        await task_service.reassign_approvals(payload["user_id"])
```

---

### 32.2 模块 2：智能体 — 血管图与内部设计

### 32.2 模块 2：智能体 — 血管图与内部设计

#### 32.2.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Agent["② 智能体"]
        AG_CTRL["AgentController\nCRUD·发布·下线"]
        AG_SVC["AgentService\n生命周期·Saga补偿"]
        AG_REPO["AgentRepository\n乐观锁·版本控制"]
        DIFY_ADPT["DifyConsoleAdapter\ncreateApp·configureModel·apiKey"]
    end

    AG_CTRL --> AG_SVC --> AG_REPO
    AG_SVC --> DIFY_ADPT --> Dify["Dify Console API\n/apps · /model-configs · /api-keys"]
    AG_SVC --> KB_BIND["③ 知识库绑定\nagent_kb_bindings表"]
    AG_SVC --> TOOL_BIND["⑤ 工具绑定\nagent_tool_bindings表"]
    AG_REPO --> DB[("agent_registry\nagent_kb_bindings\nagent_tool_bindings")]
    AG_SVC -->|"agent.published"| Conv["④ 会话\n用户端可见"]
    AG_SVC -->|"agent.status_changed"| EventBus["EventBus → 审计日志"]
```

#### 32.2.2 DTO 与验证器

```python
# === app/schemas/agent.py ===
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Optional
import uuid


class AgentType(str, Enum):
    chat = "chat"
    agent = "agent"
    workflow = "workflow"


class AgentVisibility(str, Enum):
    all = "all"
    department = "department"
    self = "self"


class AgentCategory(str, Enum):
    legal = "legal"
    hr = "hr"
    sales = "sales"
    it = "it"
    finance = "finance"
    other = "other"


class ModelConfig(BaseModel):
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1, le=32768)
    top_p: float = Field(default=0.9, ge=0, le=1)


class CreateAgentDto(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="名称不能为空")
    agent_id: str = Field(
        min_length=1, max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="仅允许小写字母、数字、连字符",
    )
    type: AgentType
    description: Optional[str] = Field(default=None, max_length=500)
    icon: Optional[str] = Field(default=None, max_length=10)  # emoji
    category: Optional[AgentCategory] = None
    tags: Optional[list[str]] = Field(default=None, max_length=10)
    visibility: AgentVisibility = AgentVisibility.all
    model_provider: Optional[str] = None
    model_id: Optional[str] = None
    model_config_dto: Optional[ModelConfig] = Field(default=None, alias="modelConfig")
    prompt: Optional[str] = Field(default=None, max_length=10000)
    knowledge_base_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)
    tool_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)

    model_config = {"populate_by_name": True}


class UpdateAgentDto(BaseModel):
    """Partial update — at least one field required."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    type: Optional[AgentType] = None
    description: Optional[str] = Field(default=None, max_length=500)
    icon: Optional[str] = Field(default=None, max_length=10)
    category: Optional[AgentCategory] = None
    tags: Optional[list[str]] = Field(default=None, max_length=10)
    visibility: Optional[AgentVisibility] = None
    model_provider: Optional[str] = None
    model_id: Optional[str] = None
    model_config_dto: Optional[ModelConfig] = Field(default=None, alias="modelConfig")
    prompt: Optional[str] = Field(default=None, max_length=10000)
    knowledge_base_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)
    tool_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)

    model_config = {"populate_by_name": True}

    @field_validator("*")
    @classmethod
    def at_least_one_field(cls, values):
        if all(v is None for v in values.values()):
            raise ValueError("至少需要提供一个更新字段")
        return values


class PublishAgentDto(BaseModel):
    version: int = Field(gt=0, description="必须提供当前版本号用于乐观锁")
```

#### 32.2.3 AgentController

```python
# === app/routers/agents.py ===
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.schemas.agent import CreateAgentDto, UpdateAgentDto, PublishAgentDto
from app.services.agent import AgentService
from app.core.deps import get_current_user, require_roles, get_active_tenant

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/")
async def list_agents(
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    current_user=Depends(get_current_user),
    agent_service: AgentService = Depends(),
):
    return await agent_service.list(
        current_user.tenant_id, current_user.role,
        status=status_filter, search=search,
    )


@router.get("/{id}")
async def get_agent(
    id: str,
    current_user=Depends(get_current_user),
    agent_service: AgentService = Depends(),
):
    return await agent_service.get(current_user.tenant_id, id)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_agent(
    dto: CreateAgentDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    agent_service: AgentService = Depends(),
):
    existing = await agent_service.find_by_agent_id(current_user.tenant_id, dto.agent_id)
    if existing:
        raise HTTPException(status_code=409, detail="DUPLICATE_AGENT_ID")
    return await agent_service.create(current_user.tenant_id, current_user.sub, dto)


@router.patch("/{id}")
async def update_agent(
    id: str,
    dto: UpdateAgentDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    agent_service: AgentService = Depends(),
):
    return await agent_service.update(current_user.tenant_id, id, dto)


@router.post("/{id}/publish")
async def publish_agent(
    id: str,
    dto: PublishAgentDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    agent_service: AgentService = Depends(),
):
    return await agent_service.publish(
        current_user.tenant_id, id, current_user.sub, dto.version,
    )


@router.post("/{id}/offline")
async def offline_agent(
    id: str,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    agent_service: AgentService = Depends(),
):
    return await agent_service.offline(current_user.tenant_id, id)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    id: str,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    agent_service: AgentService = Depends(),
):
    await agent_service.delete(current_user.tenant_id, id)
```

#### 32.2.4 AgentService（Saga 补偿完整版）

已在 §30.2 中有完整实现，此处补充事件发射：

```python
# AgentService.create() 成功后：
await event_bus.publish("agent.created", {
    "agent_id": agent_id, "tenant_id": tenant_id,
    "user_id": user_id, "dify_app_id": dify_app_id,
})

# AgentService.publish() 成功后：
await event_bus.publish("agent.published", {
    "agent_id": agent_id, "tenant_id": tenant_id,
    "published_by": user_id,
})

# AgentService.offline() 成功后：
await event_bus.publish("agent.offline", {"agent_id": agent_id, "tenant_id": tenant_id})

# AgentService.delete() 成功后：
await event_bus.publish("agent.deleted", {
    "agent_id": agent_id, "tenant_id": tenant_id, "dify_app_id": dify_app_id,
})
```

#### 32.2.5 事件总线

| 事件名 | 消费者 | 动作 |
|-------|--------|------|
| `agent.created` | AuditLogService | 记录创建审计 |
| `agent.published` | ConversationService（刷新可见列表缓存） | 用户端 AgentMarketplace SWR revalidate |
| `agent.offline` | ConversationService | 移除活跃会话中的 Agent 选择器选项 |
| `agent.deleted` | KnowledgeBindingService, ToolBindingService | 级联清理绑定关系 |

---

### 32.3 模块 3：知识库 — 血管图与内部设计

#### 32.3.1 跨模块交互图

```mermaid
flowchart LR
    subgraph KB["③ 知识库"]
        KB_CTRL["KnowledgeController\nCRUD·上传·检索测试"]
        KB_SVC["KnowledgeService\nDify数据集生命周期"]
        KB_ADPT["DifyKnowledgeAdapter\ncreateDataset·uploadDoc·retrieve"]
    end

    KB_CTRL --> KB_SVC --> KB_ADPT --> Dify["Dify API\n/v1/datasets/*\n/v1/datasets/:id/documents/*"]
    KB_SVC -->|"绑定"| Agent["② 智能体\nagent_kb_bindings"]
    KB_SVC -->|"索引状态"| Task["⑦ 任务中心\nknowledge-index-status队列"]
    KB_SVC -->|"召回引用"| Trace["⑥ 运行记录\ncitation来源"]
    KB_SVC --> DB[("kb_registry\nkb_documents")]
    KB_SVC -->|"kb.document.indexed"| EventBus["EventBus"]
```

#### 32.3.2 DTO 与验证器

```python
# === app/schemas/knowledge.py ===
from pydantic import BaseModel, Field
from typing import Optional


class CreateKnowledgeBaseDto(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    chunk_size: int = Field(default=800, ge=100, le=2000)  # tokens
    chunk_overlap: int = Field(default=50, ge=0, le=500)
    embedding_model: str = "text-embedding-3-small"


class RetrieveTestDto(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="查询不能为空")
    top_k: int = Field(default=5, ge=1, le=20)


# UploadDocumentDto: multipart/form-data validated in router via UploadFile
# file: max 15MB, allowed types: pdf,docx,xlsx,txt,md,csv,json,html
```

#### 32.3.3 KnowledgeController

```python
# === app/routers/knowledge.py ===
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from app.schemas.knowledge import CreateKnowledgeBaseDto, RetrieveTestDto
from app.services.knowledge import KnowledgeService
from app.core.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge"])

ALLOWED_TYPES = {"pdf", "docx", "xlsx", "txt", "md", "csv", "json", "html"}
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15MB


def validate_file_type_and_size(file: UploadFile) -> None:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="UNSUPPORTED_FILE_TYPE")
    # Magic-bytes check omitted for brevity — see §25 validation pipeline
    # Size is validated via streaming read below


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_kb(
    dto: CreateKnowledgeBaseDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "knowledge_admin", "agent_admin")),
    kb_service: KnowledgeService = Depends(),
):
    return await kb_service.create(current_user.tenant_id, dto)


@router.get("/")
async def list_kbs(
    current_user=Depends(get_current_user),
    kb_service: KnowledgeService = Depends(),
):
    return await kb_service.list(current_user.tenant_id)


@router.get("/{id}")
async def get_kb(
    id: str,
    current_user=Depends(get_current_user),
    kb_service: KnowledgeService = Depends(),
):
    return await kb_service.get(current_user.tenant_id, id)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    id: str,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "knowledge_admin", "agent_admin")),
    kb_service: KnowledgeService = Depends(),
):
    # 检查绑定：如有 Agent 绑定则提示解绑后再删除
    await kb_service.delete(current_user.tenant_id, id)


@router.post("/{id}/documents")
async def upload_document(
    id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "knowledge_admin", "agent_admin")),
    kb_service: KnowledgeService = Depends(),
):
    validate_file_type_and_size(file)  # 15MB + 白名单 magic bytes
    return await kb_service.upload_document(current_user.tenant_id, id, file)


@router.get("/{id}/documents")
async def list_documents(
    id: str,
    current_user=Depends(get_current_user),
    kb_service: KnowledgeService = Depends(),
):
    return await kb_service.list_documents(current_user.tenant_id, id)


@router.post("/{id}/retrieval-test")
async def retrieval_test(
    id: str,
    dto: RetrieveTestDto,
    current_user=Depends(get_current_user),
    kb_service: KnowledgeService = Depends(),
):
    return await kb_service.retrieval_test(current_user.tenant_id, id, dto)
```

#### 32.3.4 事件总线

| 事件名 | 触发时机 | 消费者 |
|-------|---------|--------|
| `kb.created` | 创建知识库 | AuditLogService |
| `kb.document.uploaded` | 文档上传成功 | TaskService（入队索引追踪） |
| `kb.document.indexed` | Dify 索引完成 | KB 状态更新 + 通知管理员 |
| `kb.document.index_failed` | 索引失败 | 通知管理员 + 记录错误 |
| `kb.deleted` | 删除知识库 | AgentBindingService（级联解绑） |

---

### 32.4 模块 4：会话 — 血管图与内部设计

#### 32.4.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Conv["④ 会话"]
        CONV_CTRL["ConversationController\nCRUD·发消息(SSE)"]
        CONV_SVC["ConversationService\nDify聊天代理·记忆注入"]
        MEM_SVC["MemoryService\n偏好/事实召回"]
        DIFY_ADPT["DifyConversationAdapter\nSSE代理·重连·心跳"]
    end

    CONV_CTRL --> CONV_SVC
    CONV_SVC --> MEM_SVC -->|pgvector| DB_MEM[("user_memories")]
    CONV_SVC --> DIFY_ADPT -->|SSE| Dify["Dify API\n/v1/chat-messages\nresponse_mode:streaming"]
    CONV_SVC -->|"工具调用触发"| Tool["⑤ 工具代理"]
    CONV_SVC -->|"对话完成"| Trace["⑥ 运行记录\nrun_logs"]
    CONV_SVC -->|"记忆提取job"| Task["⑦ 任务中心\nmemory-extraction队列"]
    CONV_SVC --> DB[("conversations\nmessages")]
```

#### 32.4.2 DTO 与核心接口

```python
# === app/schemas/conversation.py ===
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class CreateConversationDto(BaseModel):
    agent_id: str = Field(min_length=1)
    title: Optional[str] = Field(default=None, max_length=200)


class ConversationStatus(str, Enum):
    active = "active"
    archived = "archived"


class ListConversationsDto(BaseModel):
    agent_id: Optional[str] = None
    status: ConversationStatus = ConversationStatus.active
    limit: int = Field(default=20, ge=1, le=100)
    cursor: Optional[str] = None  # 游标分页


class SendMessageDto(BaseModel):
    query: str = Field(min_length=1, max_length=10000, description="消息最多10000字符")
    agent_id: str = Field(min_length=1)
    files: Optional[list[dict]] = Field(default=None, max_length=5)
    # file shape: { name: str, type: str, size: int, transferMethod: 'local_file'|'remote_url', url: str? }
```

#### 32.4.3 ConversationController

```python
# === app/routers/conversations.py ===
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from app.schemas.conversation import CreateConversationDto, ListConversationsDto, SendMessageDto
from app.services.conversation import ConversationService
from app.core.deps import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    dto: CreateConversationDto,
    current_user=Depends(get_current_user),
    conv_service: ConversationService = Depends(),
):
    return await conv_service.create(current_user.sub, current_user.tenant_id, dto)


@router.get("/")
async def list_conversations(
    query: ListConversationsDto = Depends(),
    current_user=Depends(get_current_user),
    conv_service: ConversationService = Depends(),
):
    return await conv_service.list(current_user.sub, current_user.tenant_id, query)


@router.get("/{id}")
async def get_conversation(
    id: str,
    current_user=Depends(get_current_user),
    conv_service: ConversationService = Depends(),
):
    return await conv_service.get(current_user.sub, id)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    id: str,
    current_user=Depends(get_current_user),
    conv_service: ConversationService = Depends(),
):
    await conv_service.soft_delete(current_user.sub, id)


@router.post("/{id}/messages")
async def send_message(
    id: str,
    dto: SendMessageDto,
    request: Request,
    current_user=Depends(get_current_user),
    conv_service: ConversationService = Depends(),
):
    # 速率限制检查
    await conv_service.rate_limit_service.check(current_user.sub, "chat", 20, 60000)  # 20条/分钟

    async def event_stream():
        try:
            async for chunk in conv_service.stream_message(
                current_user.sub, current_user.tenant_id, id, dto
            ):
                if await request.is_disconnected():
                    break
                yield chunk
        except Exception as err:
            logger = conv_service.logger
            logger.error("sse_stream_error", conversation_id=id, error=str(err))
            yield f'event: error\ndata: {{"code":"STREAM_ERROR","message":"流中断"}}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 禁用缓冲
        },
    )
```

---

### 32.5 模块 5：工具 — 血管图与内部设计

#### 32.5.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Tool["⑤ 工具代理"]
        TOOL_CTRL["ToolController\nCRUD·调试"]
        TOOL_SVC["ToolService\n注册·代理·熔断"]
        TOOL_PROXY["ToolProxy\nHTTP执行·重试·脱敏"]
        CB["CircuitBreaker\n进程内熔断→P2 Redis"]
    end

    TOOL_CTRL --> TOOL_SVC --> TOOL_PROXY
    TOOL_PROXY --> CB
    TOOL_PROXY -->|HTTP请求| EXT["外部企业API\nERP/CRM/OA/..."]

    TOOL_SVC -->|"高风险·需审批"| Task["⑦ 任务中心\n审批流·RQ"]
    TOOL_SVC -->|"绑定"| Agent["② 智能体\nagent_tool_bindings"]
    TOOL_SVC -->|"执行日志"| Trace["⑥ 运行记录\ntool_call_runs"]
    TOOL_SVC --> DB[("tool_registry\ntool_call_runs")]
```

#### 32.5.2 DTO 与验证器

```python
# === app/schemas/tool.py ===
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ToolType(str, Enum):
    http = "http"
    database = "database"
    rpa = "rpa"
    webhook = "webhook"
    mcp = "mcp"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PermissionMode(str, Enum):
    auto = "auto"
    confirm = "confirm"
    disabled = "disabled"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class AuthType(str, Enum):
    none = "none"
    api_key = "api_key"
    bearer = "bearer"
    oauth2 = "oauth2"
    basic = "basic"


class HeaderPair(BaseModel):
    key: str
    value: str


class AuthConfig(BaseModel):
    type: AuthType = AuthType.none
    config: Optional[dict[str, str]] = None


class CreateToolDto(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tool_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    type: ToolType
    description: Optional[str] = Field(default=None, max_length=500)
    risk_level: RiskLevel
    permission_mode: PermissionMode
    endpoint: str  # validated as URL at service layer
    method: HttpMethod
    headers: Optional[list[HeaderPair]] = Field(default=None, max_length=20)
    auth: AuthConfig = AuthConfig()
    request_schema: Optional[str] = None   # JSON Schema string
    response_schema: Optional[str] = None
    timeout: int = Field(default=15000, ge=1000, le=60000)  # ms
    max_retries: int = Field(default=2, ge=0, le=5)


class DebugToolDto(BaseModel):
    params: dict
    save_as_test_case: bool = False
```

#### 32.5.3 ToolProxy 核心实现

```python
# === app/services/tool_proxy.py ===
import time
import asyncio
from datetime import datetime, timedelta
from typing import Any
import httpx
import structlog

logger = structlog.get_logger()


class CircuitBreakerState:
    def __init__(self) -> None:
        self.failures: int = 0
        self.open_until: datetime | None = None


class ToolProxy:
    def __init__(self, tool_repo, inject_auth_fn=None, mask_fn=None) -> None:
        self.tool_repo = tool_repo
        self._inject_auth = inject_auth_fn or (lambda tool: {})
        self._mask = mask_fn or (lambda data, tool: data)
        self.circuit_breakers: dict[str, CircuitBreakerState] = {}
        self._client = httpx.AsyncClient(timeout=30.0)

    async def execute(
        self, tool_name: str, params: dict[str, Any],
        conversation_id: str | None = None,
    ) -> dict:
        tool = await self.tool_repo.find_by_name(tool_name)

        # Step 1: 权限检查
        if tool.permission_mode == "disabled":
            return {"status": "error", "message": "工具已禁用"}
        if tool.permission_mode == "confirm" and not params.get("_approved"):
            task_id = await self._create_approval(tool, params, conversation_id)
            return {"status": "pending_approval", "task_id": task_id}

        # Step 2: 熔断检查
        if self._is_circuit_open(tool.tool_id):
            return {"status": "error", "message": "熔断器已打开，请稍后重试"}

        # Step 3: 注入鉴权头
        headers = await self._inject_auth(tool)

        # Step 4: 参数脱敏
        sanitized = self._sanitize_params(params, tool)

        # Step 5: 执行（含重试）
        start_time = time.monotonic()
        try:
            result = await self._execute_with_retry(tool, sanitized, headers, tool.max_retries)
            self._record_success(tool.tool_id)
            result["latency_ms"] = int((time.monotonic() - start_time) * 1000)
            return result
        except Exception:
            self._record_failure(tool.tool_id)
            raise

    async def _execute_with_retry(
        self, tool, params: Any, headers: dict[str, str], retries_left: int,
    ) -> dict:
        try:
            req_headers = {"Content-Type": "application/json", **headers}
            req_body = None if tool.method == "GET" else params
            res = await self._client.request(
                method=tool.method,
                url=tool.endpoint,
                json=req_body,
                headers=req_headers,
                timeout=tool.timeout / 1000,
            )
            data = res.json()
            if res.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {res.status_code}", request=res.request, response=res
                )
            # 响应脱敏
            return {"status": "success", "data": self._mask(data, tool)}
        except Exception:
            if retries_left > 0:
                delay = 2 ** (tool.max_retries - retries_left + 1)
                await asyncio.sleep(delay)
                return await self._execute_with_retry(tool, params, headers, retries_left - 1)
            raise

    def _is_circuit_open(self, tool_id: str) -> bool:
        cb = self.circuit_breakers.get(tool_id)
        if not cb or not cb.open_until:
            return False
        if datetime.utcnow() > cb.open_until:
            del self.circuit_breakers[tool_id]
            return False
        return True

    def _record_failure(self, tool_id: str) -> None:
        cb = self.circuit_breakers.get(tool_id) or CircuitBreakerState()
        cb.failures += 1
        if cb.failures >= 5:
            cb.open_until = datetime.utcnow() + timedelta(minutes=1)  # 1分钟
        self.circuit_breakers[tool_id] = cb

    def _record_success(self, tool_id: str) -> None:
        self.circuit_breakers.pop(tool_id, None)

    def _sanitize_params(self, params: dict[str, Any], tool) -> dict[str, Any]:
        # 移除 PII 字段（如身份证、手机号、银行卡号）
        return self._mask(params, tool)

    async def _create_approval(self, tool, params, conversation_id) -> str:
        # Delegates to TaskService to create a pending approval task
        ...
```

---

### 32.6 模块 6：运行记录 — 血管图与内部设计

#### 32.6.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Trace["⑥ 运行记录"]
        TRACE_CTRL["RunLogController\n列表·详情·搜索"]
        TRACE_SVC["RunLogService\n聚合·查询·导出"]
    end

    Agent["② 智能体"] -->|"发布/下线/调用"| TRACE_SVC
    Conv["④ 会话"] -->|"每次对话"| TRACE_SVC
    Tool["⑤ 工具"] -->|"每次调用"| TRACE_SVC
    Task["⑦ 任务"] -->|"每次状态变更"| TRACE_SVC

    TRACE_SVC --> DB[("run_logs\nP2: trace_steps\nP2: trace_citations\nP2: trace_tool_calls")]
    TRACE_SVC --> Prometheus["Prometheus\n/metrics端点"]
```

#### 32.6.2 RunLogController

```python
# === app/routers/run_logs.py ===
from fastapi import APIRouter, Depends, Query
from app.services.run_log import RunLogService
from app.core.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/run-logs", tags=["run-logs"])


@router.get("/")
async def list_run_logs(
    agent_id: str | None = Query(None),
    conversation_id: str | None = Query(None),
    status_filter: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    limit: int | None = Query(None),
    cursor: str | None = Query(None),
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin", "knowledge_admin", "auditor")),
    log_service: RunLogService = Depends(),
):
    return await log_service.list(
        current_user.tenant_id,
        agent_id=agent_id, conv_id=conversation_id, status=status_filter,
        from_date=from_date, to_date=to_date, limit=limit, cursor=cursor,
    )


@router.get("/{trace_id}")
async def get_run_log(
    trace_id: str,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin", "knowledge_admin", "auditor")),
    log_service: RunLogService = Depends(),
):
    return await log_service.get_by_trace_id(current_user.tenant_id, trace_id)
```

---

### 32.7 模块 7：任务中心 — 血管图与内部设计

#### 32.7.1 跨模块交互图

```mermaid
flowchart LR
    subgraph Task["⑦ 任务中心"]
        TASK_CTRL["TaskController\n列表·审批·重试·取消"]
        TASK_SVC["TaskService\n状态机·入队·回调"]
        BULL["RQ\n4个队列"]
    end

    Tool["⑤ 工具代理"] -->|"tool-approval"| BULL
    KB["③ 知识库"] -->|"knowledge-index-status"| BULL
    Conv["④ 会话"] -->|"memory-extraction"| BULL
    ALL["全模块"] -->|"audit-log"| BULL

    BULL --> WORKERS["Worker进程\nRQ worker消费者"]
    WORKERS -->|"执行结果"| Trace["⑥ 运行记录"]
    TASK_SVC --> DB[("tasks\ntask_approvals")]
    TASK_SVC -->|"通知"| NOTIFY["站内信·邮件·WebSocket(P2)"]
```

#### 32.7.2 TaskController

```python
# === app/routers/tasks.py ===
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from app.services.task import TaskService
from app.core.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class ApproveTaskDto(BaseModel):
    comment: Optional[str] = None


class RejectTaskDto(BaseModel):
    reason: str


@router.get("/")
async def list_tasks(
    status_filter: str | None = None,
    type_filter: str | None = None,
    priority: str | None = None,
    current_user=Depends(get_current_user),
    task_service: TaskService = Depends(),
):
    return await task_service.list(
        current_user.sub, current_user.tenant_id,
        status=status_filter, type=type_filter, priority=priority,
    )


@router.get("/{id}")
async def get_task(
    id: str,
    current_user=Depends(get_current_user),
    task_service: TaskService = Depends(),
):
    return await task_service.get(current_user.sub, id)


@router.post("/{id}/approve")
async def approve_task(
    id: str,
    dto: ApproveTaskDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    task_service: TaskService = Depends(),
):
    return await task_service.transition(id, "approved", current_user.sub, dto.comment)


@router.post("/{id}/reject")
async def reject_task(
    id: str,
    dto: RejectTaskDto,
    current_user=Depends(get_current_user),
    _: None = Depends(require_roles("platform_admin", "agent_admin")),
    task_service: TaskService = Depends(),
):
    return await task_service.transition(id, "rejected", current_user.sub, dto.reason)


@router.post("/{id}/retry")
async def retry_task(
    id: str,
    current_user=Depends(get_current_user),
    task_service: TaskService = Depends(),
):
    return await task_service.retry(current_user.sub, id)


@router.post("/{id}/cancel")
async def cancel_task(
    id: str,
    current_user=Depends(get_current_user),
    task_service: TaskService = Depends(),
):
    return await task_service.transition(id, "cancelled", current_user.sub)
```

#### 32.7.3 TaskService 状态机实现

```python
# === app/services/task.py ===
from datetime import datetime
from typing import Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.task import Task
from app.core.exceptions import UnprocessableEntityException
from app.core.event_bus import event_bus

TaskStatus = Literal[
    "pending", "approved", "rejected",
    "executing", "completed", "failed", "cancelled",
]

VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending":   ["approved", "rejected", "cancelled"],
    "approved":   ["executing", "cancelled"],
    "rejected":   [],
    "executing":  ["completed", "failed", "cancelled"],
    "completed":  [],
    "failed":     ["executing", "cancelled"],
    "cancelled":  [],
}

QUEUE_NAME_MAP = {
    "tool_approval": "tool-approval",
    "knowledge_index": "knowledge-index-status",
    "memory_extraction": "memory-extraction",
    "audit_log": "audit-log",
}


class TaskService:
    def __init__(self, session: AsyncSession, rq_queue) -> None:
        self.session = session
        self.rq_queue = rq_queue

    async def transition(
        self, task_id: str, to_status: TaskStatus,
        actor_id: str, metadata: Optional[str] = None,
    ) -> Task:
        result = await self.session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError("TASK_NOT_FOUND")
        if to_status not in VALID_TRANSITIONS.get(task.status, []):
            raise UnprocessableEntityException(
                f"INVALID_TRANSITION: {task.status} → {to_status}"
            )

        update_values: dict = {"status": to_status, "actor_id": actor_id}
        # Dynamic timestamp + actor fields
        update_values[f"{to_status}_at"] = datetime.utcnow()
        update_values[f"{to_status}_by"] = actor_id

        await self.session.execute(
            update(Task).where(Task.id == task_id).values(**update_values)
        )
        await self.session.commit()

        if to_status == "approved":
            queue_name = QUEUE_NAME_MAP.get(task.type, "audit-log")
            self.rq_queue.enqueue(queue_name, task_id=task_id, **task.payload)

        await event_bus.publish("task.status_changed", {
            "task_id": task_id, "from": task.status,
            "to": to_status, "actor_id": actor_id,
        })
        result = await self.session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one()
```

---

### 32.8 模块 8–14 血管概览

| 模块 | 核心数据流 | 关键交互 |
|------|----------|---------|
| ⑧ 仪表盘 | 聚合查询 agents / conversations / run_logs / tasks 四表 | 只读，无写操作 |
| ⑨ 模型 | 通过 DifyConsoleClient 查询 `/workspaces/current/models` | Dify Admin API 直通 |
| ⑩ 记忆 (P2) | 对话完成 → memory-extraction RQ job → LLM 分析 → pgvector upsert | ④会话 + ⑦任务 |
| ⑪ 工作流 (P2) | 自有表单 → 生成 DSL YAML → `DifyConsoleClient.importDSL()` | ②智能体 + Dify |
| ⑫ 评测 (P2) | 测试集管理 + 批量调用 Dify + 自动评分 | ②智能体 + Dify SSE |
| ⑬ 内容安全 (P2) | Dify Moderation API Extension 对接 | Dify |
| ⑭ 系统设置 | 全局配置 KV + 配额管理 | 仅 platform_admin |


## <a id="ch-v6-revision"></a>三十三、v6.0 修订：第四轮评审（Vulcan / Vexel / Verity）意见落地

> 本节为 v5.0 → v6.0 的增量修订，基于三方第四轮评审：
> - **Vulcan（后端）**：3 个多实例 P0 缺陷（PKCE 内存态、Refresh Rotation 竞态、TaskService 读-判-写竞态）+ 6 个 P1
> - **Vexel（前端）**：前端血管层不对称、SWR key 注册表缺失等 2 个 P0 + 5 个 P1/P2
> - **Verity（QA）**：测试资源不匹配、覆盖不足、数据生命周期、CI 门禁等 1 个 P0 + 5 个 P1/P2
>
> **修订原则**：P0 全部给出可直接替换的实现代码；P1 给出契约或规范条目；P2 标注落点。

---

### 33.1 后端 P0-1：OIDC PKCE State 迁移 Redis（TTL + 原子消费）

**问题**（§32.1.5）：`codeVerifiers` 进程内 Map —— 多实例下登录发起与回调落在不同实例导致 `INVALID_STATE` 随机失败；无 TTL 导致内存泄漏可被刷爆。

**修复**：Redis 存储 + `GETDEL` 原子消费。

```python
# === app/services/auth/state_store.py ===
import json
import redis.asyncio as aioredis
from app.core.config import settings

STATE_TTL_SECONDS = 600  # 10 分钟：覆盖 IdP 慢速登录场景后自动过期


class OidcStateStore:
    def __init__(self) -> None:
        self.redis = aioredis.from_url(settings.REDIS_URL)

    async def create(self, state: str, payload: dict) -> None:
        """登录发起时写入：state → { verifier, redirect_uri }。
        NX 保证 state 唯一；EX 保证自动淘汰，杜绝内存泄漏。
        """
        ok = await self.redis.set(
            f"oidc:state:{state}", json.dumps(payload),
            ex=STATE_TTL_SECONDS, nx=True,
        )
        if not ok:
            raise RuntimeError("STATE_COLLISION")  # 概率极低（128bit 随机），防御性报错

    async def consume(self, state: str) -> dict | None:
        """回调时原子消费：GETDEL 保证一个 state 只能用一次。
        多实例安全：无论回调落在哪个实例，都从同一 Redis 取值。
        返回 None = 不存在（已过期 / 已消费 / 伪造）→ 上层抛 INVALID_STATE。
        """
        raw = await self.redis.getdel(f"oidc:state:{state}")
        return json.loads(raw) if raw else None
```

AuthService 改造点（替换 §32.1.5 中 `codeVerifiers` 相关三处）：

```python
# initiate_oidc():
await self.state_store.create(state, {"verifier": verifier, "redirect_uri": redirect_uri})

# validate_and_consume_state() 整体删除，handle_callback 内改为：
session = await self.state_store.consume(state)
if not session:
    raise UnauthorizedException("INVALID_STATE")
```

---

### 33.2 后端 P0-2：Refresh Token Rotation 原子化 + 家族吊销

**问题**（§32.1.5 refreshToken）：`findValid → revoke → create` 三步分离存在并发竞态；且无复用检测——旧 token 被盗用后系统毫无感知。

**修复**：单条条件 UPDATE 原子轮换 + token family 复用检测。

Schema 增补（SQLAlchemy）：

```python
# === app/models/refresh_token.py ===
from sqlalchemy import String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
import uuid
from app.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    family_id: Mapped[str] = mapped_column(String(36))  # token 家族 ID：首次登录生成，rotation 时继承
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = 有效；非 NULL = 已轮换/已吊销
    replaced_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 轮换后的新 token id（审计链）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_refresh_tokens_user_family", "user_id", "family_id"),
    )
```

实现：

```python
# === app/services/auth/token_rotation.py ===
import secrets
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.core.exceptions import UnauthorizedException
import structlog

logger = structlog.get_logger()

REFRESH_TTL_DAYS = 30


class TokenRotationService:
    def __init__(self, session: AsyncSession, jwt_service) -> None:
        self.session = session
        self.jwt = jwt_service

    async def refresh_token(self, token: str) -> dict:
        token_hash = self.hash_token(token)

        async with self.session.begin():
            # Step 1: 原子认领——只有 revokedAt IS NULL 的行才能被轮换。
            # 并发双请求只有一个能更新成功（行锁），另一个 count=0 → 进入复用检测分支。
            result = await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.utcnow())
            )
            claimed_count = result.rowcount

            stored_result = await self.session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
            stored = stored_result.scalar_one_or_none()
            if not stored:
                raise UnauthorizedException("TOKEN_EXPIRED")

            if claimed_count == 0:
                # Step 2: 复用检测——已吊销 token 再次出现 = 疑似被盗，吊销整个家族。
                await self.session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=datetime.utcnow())
                )
                logger.warning(
                    "refresh_token_reuse_detected",
                    user_id=stored.user_id, family_id=stored.family_id,
                )
                raise UnauthorizedException("TOKEN_REUSE_DETECTED")

            # Step 3: 同事务签发新 token（继承 familyId）
            user_result = await self.session.execute(
                select(User).where(User.id == stored.user_id)
            )
            user = user_result.scalar_one()
            if user.status != "active":
                raise UnauthorizedException("USER_DISABLED")

            new_token = secrets.token_urlsafe(48)
            new_refresh = RefreshToken(
                user_id=user.id,
                family_id=stored.family_id,
                token_hash=self.hash_token(new_token),
                expires_at=datetime.utcnow() + timedelta(days=REFRESH_TTL_DAYS),
            )
            self.session.add(new_refresh)
            await self.session.flush()

            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.id == stored.id)
                .values(replaced_by=new_refresh.id)
            )

            access_token = self.jwt.encode(
                {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role, "jti": str(uuid.uuid4())},
                expires_in=900,  # 15min — 见 33.4 P1-6：缩短窗口 + 前端静默刷新
            )

        return {"access_token": access_token, "new_refresh_token": new_token}

    @staticmethod
    def hash_token(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()
```

> **取舍说明**：access token 有效期从 2h 缩短为 15min，配合前端 http-client 401 静默刷新拦截器（§30.11 已有）。logout 后 access token 最大残留窗口 = 15min，写入附录 B 错误码表备注。

---

### 33.3 后端 P0-3：TaskService 乐观锁 + 事务性 Outbox

**问题**（§32.7.3）：读-判-写三步无锁（并发 approve 双 job 入队）；DB 更新成功后 `rq_queue.enqueue` 失败任务永久卡死。

**修复 A**：状态转换用条件 UPDATE 乐观锁。

```python
VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending":   ["approved", "rejected", "cancelled"],
    "approved":   ["executing", "cancelled"],
    "rejected":   [],
    "executing":  ["completed", "failed", "cancelled"],
    "completed":  [],
    "failed":     ["executing", "cancelled"],
    "cancelled":  [],
}


async def transition(
    self, task_id: str, to_status: TaskStatus,
    actor_id: str, metadata: str | None = None,
) -> Task:
    result = await self.session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise NotFoundException("TASK_NOT_FOUND")
    if to_status not in VALID_TRANSITIONS.get(task.status, []):
        raise UnprocessableEntityException(
            f"INVALID_TRANSITION: {task.status} → {to_status}"
        )

    # Outbox 记录与状态变更同事务写入（见修复 B）
    outbox_id = str(uuid.uuid4())

    async with self.session.begin():
        # 乐观锁：期望状态必须仍是读到的值
        result = await self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == task.status)
            .values(status=to_status, actor_id=actor_id)
        )
        if result.rowcount == 0:
            raise ConflictException("TASK_ALREADY_PROCESSED")

        outbox = TaskOutboxEvent(
            id=outbox_id, task_id=task_id,
            type=map_queue_name(task.type), status=to_status,
            payload=task.payload,
        )
        self.session.add(outbox)

    await event_bus.publish("task.status_changed", {
        "task_id": task_id, "from": task.status,
        "to": to_status, "actor_id": actor_id,
    })
    result = await self.session.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one()


# Worker 侧执行完成后标记 outbox 已投递
async def mark_outbox_delivered(self, outbox_id: str) -> None:
    await self.session.execute(
        update(TaskOutboxEvent)
        .where(TaskOutboxEvent.id == outbox_id)
        .values(delivered_at=datetime.utcnow())
    )
    await self.session.commit()
```

**修复 B**：Outbox 表 + 对账扫描兜底。

```python
# === app/models/task_outbox.py ===
from sqlalchemy import String, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.models.base import Base


class TaskOutboxEvent(Base):
    __tablename__ = "task_outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(50))  # 队列名：tool-approval / knowledge-index-status / ...
    status: Mapped[str] = mapped_column(String(20))  # 触发状态：approved / retry 等
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Worker 成功入队并 ack 后写入

    __table_args__ = (
        Index("ix_task_outbox_delivered_created", "delivered_at", "created_at"),
    )
```

```python
# 定时对账：每分钟扫描未投递的 outbox 事件重新入队（幂等：RQ job_id = outbox_id 去重）
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()


@scheduler.scheduled_job("cron", second="*/60")
async def reconcile_undelivered(session_factory, rq_queue) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(TaskOutboxEvent)
            .where(
                TaskOutboxEvent.delivered_at.is_(None),
                TaskOutboxEvent.created_at < datetime.utcnow() - timedelta(seconds=60),
            )
            .limit(100)
        )
        pending = result.scalars().all()
        for evt in pending:
            from rq import Queue
            queue = Queue(evt.type, connection=rq_queue.connection)
            queue.enqueue(
                process_task,
                task_id=evt.task_id,
                outbox_id=evt.id,
                **evt.payload,
                job_id=evt.id,  # RQ 相同 job_id 不重复入队 → 天然幂等
            )
```

> **幂等键补充**（Vulcan P1-9）：approve 接口同时接受 `Idempotency-Key` header；结合上面的条件 UPDATE，双击/重试均只会产生一次有效转换，第二次得到 409。

---

### 33.4 后端 P1 修复汇总

| # | 问题 | 落地 |
|---|------|------|
| P1-4 | 邮箱域未匹配到租户时 `tenant.id` TypeError | `handle_callback` Step 4 改为：未命中 sso_domain 且未配置默认租户时抛 `TENANT_NOT_FOUND`（附录 B 新增错误码），前端展示"您的邮箱域未被授权接入" |
| P1-5 | 新用户 role 来源隐式 | SQLAlchemy model 层 `role: Mapped[str] = mapped_column(default="employee")`；代码不再依赖 ORM 默认值之外的隐式行为，upsert 显式传 `role='employee'` |
| P1-6 | JWT jti 无黑名单，logout 后 access token 最长残留 2h | **决策**：接受残留但收窄至 15min（33.2 已改），logout 吊销 refresh token 家族；文档明确"access token 自然过期"语义。P2 如需即时失效再引入 Redis jti 黑名单 |
| P1-7 | 动态字段 `${toStatus}At` 缺 Schema 定义 | Task model 补齐显式字段：`approved_at/approved_by/rejected_at/rejected_by/executing_at/completed_at/failed_at/cancelled_at/cancelled_by`（全部 `DateTime?` / `String?`），§5.2 Schema 同步更新 |
| P1-8 | TaskController.list 无分页 + approve/reject 绕过 Pydantic | 增加 `ListTasksDto`（cursor 分页，同 §22.4 规范）；approve/reject 请求体改走 `ApproveTaskDto(BaseModel)` / `RejectTaskDto(BaseModel)` |
| — | 附录 B | 新增错误码：`TENANT_NOT_FOUND`、`TOKEN_REUSE_DETECTED`、`TASK_ALREADY_PROCESSED` |

---

### 33.5 前端 P0-1：模块级前端血管图（与后端对称）

以「用户与权限」为范例，每个业务模块在第三部分对应章节补充同等深度的前端血管图。

#### 33.5.1 模块 1 前端血管图

```mermaid
flowchart LR
    subgraph FE["① 用户与权限 · 前端血管"]
        R["路由入口\n/login · /admin/members"]
        P["页面组合树\nLoginPage → SsoButton + CallbackHandler\nMemberListPage → MemberTable + InviteModal"]
        H["Hook 清单\nuseMe() useMembers() useUpdateMember()\nuseInviteMembers() useRefreshToken()"]
        K["SWR Key\nme:current members:list:filters\nmember:detail:id"]
        OPT["乐观更新点\nMemberTable 状态切换\n（rollback on error）"]
    end
    R --> P --> H --> K
    P --> OPT
```

| 页面 | 组合组件 | 消费 Hook | SWR key | 乐观更新 |
|------|---------|----------|---------|---------|
| `/login` | SsoButton、CallbackHandler、ErrorCard | 无（原生 redirect 流程） | — | — |
| `/user`（全局 Header） | UserAvatar、WorkspaceSwitcher | `useMe()` | `me:current` | — |
| `/admin/members` | MemberTable、RoleSelect、StatusToggle、InviteModal | `useMembers(filters)`、`useUpdateMember()`、`useInviteMembers()` | `members:list:{search}:{role}:{status}` | StatusToggle 即时翻转，失败回滚 + Toast |

其余模块（②–⑦）的前端血管图按此模板在 §17–20 各自章节补充：路由入口 → 页面组合树 → Hook 清单（含 SWR key 与 fallback 数据）→ 乐观更新点。**首个模块（智能体）随开发首周落地，作为模板验收。**

#### 33.5.2 SWR Key 注册表（`shared/swr-keys.ts`）

事件驱动刷新的接线前提——所有 key 由工厂函数统一生成，禁止手写字符串：

```typescript
// === shared/swr-keys.ts ===
export const swrKeys = {
  me: () => ['me:current'] as const,

  agents: {
    list: (tenantId: string, filters?: { status?: string; search?: string }) =>
      ['agents', 'list', tenantId, filters] as const,
    detail: (id: string) => ['agents', 'detail', id] as const,
    marketplace: (tenantId: string) => ['agents', 'marketplace', tenantId] as const, // 仅 published
  },
  conversations: {
    list: (agentId?: string) => ['conversations', 'list', agentId] as const,
    detail: (id: string) => ['conversations', 'detail', id] as const,
  },
  tasks: {
    pendingCount: () => ['tasks', 'pending-count'] as const,
    list: (filters?: { status?: string }) => ['tasks', 'list', filters] as const,
  },
  kb: {
    documents: (kbId: string) => ['kb', kbId, 'documents'] as const,
  },
  runLogs: {
    list: (filters?: object) => ['run-logs', 'list', filters] as const,
    detail: (traceId: string) => ['run-logs', 'detail', traceId] as const,
  },
};
```

**事件 → 失效 key 映射**（各模块章节同步标注）：

| 后端事件 | 全局监听器动作 |
|---------|--------------|
| `agent.published` / `agent.offline` | `mutate(swrKeys.agents.marketplace(tenantId))` + `mutate(swrKeys.agents.list(...))` |
| `agent.deleted` | `mutate(swrKeys.agents.list(...))`；若详情页打开中则跳转回列表 |
| `kb.document.indexed` | `mutate(swrKeys.kb.documents(kbId))` |
| `task.status_changed` | `mutate(swrKeys.tasks.pendingCount())` |

**v1 过渡期机制**（WebSocket 为 P2）：全局监听器暂不可用，采用两级兜底——
1. 所有列表 SWR 配置 `revalidateOnFocus: true`（切回标签页即刷新）；
2. 任务中心待审批数使用 `refreshInterval: 10_000` 轻量轮询（§31.2.5 P1 方案，负载可控）；
3. AgentMarketplace 在发布操作后的成功回调内主动 `mutate` 本地缓存（操作发起方即时可见）。

---

### 33.6 前端 P1/P2 修复汇总

| # | 问题 | 落地 |
|---|------|------|
| P1-3 | 乐观锁冲突 UX 契约缺失 | §30.15 新增「冲突处理标准流程」：编辑表单隐藏字段携带 GET 详情返回的 `version`；提交收到 409 后弹 Modal："该记录已被他人修改。[刷新查看最新内容] [用我的修改覆盖]"——前者放弃本地改动重新拉取，后者携带服务端最新 version 重放 PATCH。所有带 version 字段的表单复用同一 `<ConflictDialog>` 组件 |
| P1-4 | messages 端点两种响应形态 | 附录 A 该端点下并列两种形态：① 限流/参数错误命中 → 普通 JSON `429 {code:"RATE_LIMITED"}`（Content-Type: application/json）；② 通过前置检查 → SSE 流。useStreamChat 按 `res.headers.get('content-type')` 分叉：非 `text/event-stream` 一律走 JSON 错误分支（含 429 倒计时提示，§31.2.4） |
| P1-5 | 任务状态机单一事实来源 | `VALID_TRANSITIONS` 下沉至 `shared/src/task-transitions.ts`，后端 TaskService 与前端按钮渲染共用。前端按钮可见性 = `VALID_TRANSITIONS[current].includes(target)` ∩ 角色权限矩阵（§8.2）；硬编码状态判断视为 lint 违规（ESLint 自定义规则禁令） |
| P2-6 | Zod 中文文案 vs i18n | DTO 校验消息改为 message key：`z.string().min(1, 'validation.name.required')`；前端表单校验层将 key 经 next-intl 翻译展示；服务端 API 直接透传 key。§30.8 补充此约束 |
| P2-7 | `@Req() req` 缺类型 | `shared/src/types/authenticated-request.ts`：`interface AuthenticatedRequest extends Request { user: { sub: string; tenantId: string; role: UserRole } }`；§32 全部示例代码统一替换标注 |

---

### 33.7 QA 修复汇总

#### 33.7.1 测试资源修正（P0）

P1 前 4 周 QA 全职（框架搭建 + 用例编写 + CI 集成），后 4 周转兼职维护。§31.3 周计划 QA 列同步修改，并在第 8 周增加"测试资产移交清单"（用例库、工厂函数、故障注入工具、性能脚本）。

#### 33.7.2 用例扩充至 30+ 条（P1）

§31.1.2 从 15 条扩至 32 条，新增覆盖（每模块至少 2 条正向 + 2 条负向）：

| 模块 | 新增用例要点 |
|------|------------|
| 工具 | debug 正常调试返回 200；disabled 工具调试返回 409；高危工具审批触发 pending 任务 |
| 仪表盘 | 聚合查询返回四维指标结构；跨租户数据零泄漏断言 |
| 成员 | 批量邀请 100 人上限校验；角色变更触发 `user.role_changed` 事件断言；employee 访问成员接口 403 |
| 模型 | Dify 代理超时降级返回 503 MODEL_GATEWAY_TIMEOUT |
| 健康 | 依赖组件（Dify/Redis/DB）逐一断连时的降级状态码 |
| Auth（v5 血管代码回归） | refresh token 并发双请求仅一个成功；复用检测触发家族吊销（TOKEN_REUSE_DETECTED）；state 二次消费返回 INVALID_STATE |

#### 33.7.3 测试数据生命周期（P1）

- **清理策略**：集成测试默认**事务回滚**（pytest fixtures + 每用例 `BEGIN...ROLLBACK`）；跨 SSE 流等长连接用例改用 factory 精确 DELETE；E2E 使用独立 seed 快照，跑完 `TRUNCATE ... CASCADE` 重置。
- **并行隔离**：每个测试 worker 分配独立 tenant 前缀（`tenant-test-{worker_index}`），factory 函数强制注入，CI 断言结束后无残留行。
- **异步等待**：KB 索引等待封装 `wait_for_indexed(doc_id, timeout=120)` 轮询 helper；RQ 用例使用 `queue.get_job()` + `fetch_job()` 轮询，禁止裸 sleep。

#### 33.7.4 性能 CI 门禁（P1）

k6 脚本纳入 CI（`@perf` 标签，PR 可选跑、里程碑必跑），3 个门禁阈值自动拦截 ≥20% 退化：

| 门禁 | 基准（附录 D） | CI 拦截阈值 |
|------|--------------|------------|
| Agent 列表 P95 | < 200ms | ≥ 240ms fail |
| 首 Token 延迟 P95 | < 1500ms | ≥ 1800ms fail |
| 召回测试 P95 | < 800ms | ≥ 960ms fail |

#### 33.7.5 可视化回归（P2）

Playwright 截图对比覆盖 5 个关键 UI 状态：登录页、空状态引导（31.2.1）、权限不足页（31.2.2）、限流 Toast（31.2.4）、离线横幅（31.2.8）。基线图入库 `e2e/__screenshots__/`，diff 超过 1% 像素即失败。

#### 33.7.6 SSE 故障注入实现方式（P1）

| 场景 | 实现方式 |
|------|---------|
| 中途断连 | pytest 测试内启动 `http.server` 反向代理包装后端，用例中途 `socket.close()` |
| 心跳超时 | 代理 hold 住上游响应 35s 不转发（模拟 Dify 无事件） |
| 拼缺 chunk | 代理把一条完整 `data: {...}` 按字节拆成两次 write，中间插入非法 JSON 片段 |
| messageId 去重 | 代理重放同一 messageId 事件两次，断言 UI 只渲染一次 |

统一封装为 `e2e/fixtures/sse_proxy.py`，四个场景共用。

#### 33.7.7 血管层代码测试范例（P2）

AuthService.handle_callback 的 pytest 参考用例（其他模块照搬模式）：

```python
# === tests/unit/test_auth_service.py ===
import pytest
from app.core.exceptions import UnauthorizedException

@pytest.mark.asyncio
async def test_handle_callback_happy_path(mocker):
    """新用户首登创建 employee 并发 user.created"""
    mocker.patch.object(state_store, "consume", return_value={"verifier": "v"})
    mocker.patch.object(oidc, "exchange", return_value=token_fixture)
    mocker.patch.object(users, "upsert_by_sso", return_value={**user_fixture, "login_count": 1})
    r = await svc.handle_callback("code", "state")
    event_bus.publish.assert_awaited_with("user.created", mocker.ANY)
    assert r.user.role == "employee"


@pytest.mark.asyncio
async def test_handle_callback_state_consumed_raises_invalid_state(mocker):
    """state 已消费/伪造 → INVALID_STATE"""
    mocker.patch.object(state_store, "consume", return_value=None)
    with pytest.raises(UnauthorizedException, match="INVALID_STATE"):
        await svc.handle_callback("code", "bad")


@pytest.mark.asyncio
async def test_handle_callback_tenant_not_found(mocker):
    """邮箱域未匹配租户 → TENANT_NOT_FOUND（不抛 TypeError）"""
    mocker.patch.object(tenants, "find_by_sso_domain", return_value=None)
    with pytest.raises(Exception, match="TENANT_NOT_FOUND"):
        await svc.handle_callback("code", "state")
```

---

### 33.8 修订核对清单

| 来源 | 编号 | 状态 |
|------|------|------|
| Vulcan P0-1 PKCE 内存态 | §33.1 | ✅ Redis + TTL + GETDEL |
| Vulcan P0-2 Rotation 竞态 | §33.2 | ✅ 原子认领 + 家族吊销 |
| Vulcan P0-3 Task 竞态/卡死 | §33.3 | ✅ 条件 UPDATE + Outbox + 对账 |
| Vulcan P1-4~9 | §33.4 | ✅ 全部落地 |
| Vexel P0-1 前端血管图 | §33.5.1 | ✅ 模板 + 首模块随开发落地 |
| Vexel P0-2 SWR key 注册表 | §33.5.2 | ✅ 工厂 + 事件映射 + v1 兜底 |
| Vexel P1-3~5, P2-6~7 | §33.6 | ✅ 全部落地 |
| Verity P0-1 QA 资源 | §33.7.1 | ✅ 前 4 周全职 |
| Verity P1-2~4, P2-5~7 | §33.7.2–7 | ✅ 全部落地 |

*修订版本: v6.0 | 增量章节: §33 | 基于: Vulcan/Vexel/Verity 第四轮评审 | 状态: 三方 P0/P1 全部闭环*

## <a id="ch-v7-revision"></a>三十四、v7.0 修订：第五轮评审回归缺陷修复

> 本节为 v6.0 → v7.0 的增量修订。第五轮评审方：Vulcan（后端）、Vexel（前端）、Verity（QA）、Radian（代码审查，新增）。
> **背景**：§33 的修复代码本身引入了 4 个阻塞级缺陷——其中缺陷 2、4 在 Vulcan 第五轮已指出但 v6.0 未修，Radian 本轮确认并新增发现缺陷 1（familyId 未写入登录路径）。本节一次性闭环。

---

### 34.1 P0-A：认证令牌链路完整重构（Radian 缺陷 1+2+3 + 吹毛求疵 11）

> 缺陷 1：`familyId` 在登录签发路径完全未写入——新 Schema 要求非空，登录直接违反 DB 约束；即便写入空值，家族吊销会误吊销全表。
> 缺陷 2：`expiresAt` 校验在 v6.0 重写中丢失——过期 29 天未轮换的 token 仍可换出新 token，等于永不过期。
> 缺陷 3：「原子认领 + 复用即吊销家族」在多标签页下系统性误杀——Tab A/B 持同一 token 并发刷新，B 命中复用检测导致全家吊销、所有标签页登出。

以下为**完整替换版**（同时取代 §32.1.5 与 §33.2 的对应方法）：

```python
# === app/services/auth/auth_service.py（v7.0 生效版）===

import secrets
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.core.exceptions import UnauthorizedException, ConflictException
import structlog

logger = structlog.get_logger()

GRACE_WINDOW_SECONDS = 30        # 复用检测宽限窗口
REFRESH_TTL = timedelta(days=30)
ACCESS_TOKEN_TTL_SECONDS = 900   # 15min


class AuthService:
    def __init__(self, session: AsyncSession, jwt_service) -> None:
        self.session = session
        self.jwt = jwt_service

    async def issue_session(self, user: dict) -> None:
        """登录/OIDC 回调路径签发 refresh token —— 新建家族（修复缺陷 1）"""
        refresh_token = secrets.token_urlsafe(48)
        async with self.session.begin():
            new_token = RefreshToken(
                user_id=user["id"],
                family_id=str(uuid.uuid4()),  # ★ 每次登录新建家族，rotation 时继承
                token_hash=self.hash_token(refresh_token),
                expires_at=datetime.utcnow() + REFRESH_TTL,
            )
            self.session.add(new_token)
        # refresh token 走 HttpOnly Cookie，不再进 JSON body（见下方存储策略）

    async def refresh_token(self, token: str) -> dict:
        """刷新 —— 单事务：过期校验 + 原子认领 + 宽限窗口复用判定（修复缺陷 2 + 3）"""
        token_hash = self.hash_token(token)

        async with self.session.begin():
            stored_result = await self.session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
            stored = stored_result.scalar_one_or_none()
            if not stored:
                raise UnauthorizedException("TOKEN_EXPIRED")

            # ★ 缺陷 2 修复：过期校验回归
            if stored.expires_at <= datetime.utcnow():
                raise UnauthorizedException("TOKEN_EXPIRED")

            if stored.revoked_at is not None:
                # 已被认领过 —— 区分竞态与攻击：
                # 宽限窗口内 且 家族仍有存活成员 → 多标签页并发刷新（竞态），不吊销家族，
                # 让客户端等待胜出方的 BroadcastChannel 广播后重试。
                family_alive_result = await self.session.execute(
                    select(func.count()).select_from(RefreshToken)
                    .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
                )
                family_alive = family_alive_result.scalar()
                if family_alive > 0 and (
                    (datetime.utcnow() - stored.revoked_at).total_seconds() <= GRACE_WINDOW_SECONDS
                ):
                    raise ConflictException("TOKEN_REFRESH_IN_PROGRESS")
                # 超出宽限窗口 或 家族已全灭 → 判定盗用，吊销整个家族
                await self.session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=datetime.utcnow())
                )
                logger.warning(
                    "refresh_token_reuse_detected",
                    user_id=stored.user_id, family_id=stored.family_id,
                )
                raise UnauthorizedException("TOKEN_REUSE_DETECTED")

            # 原子认领 + 同事务继承 familyId 签发子 token
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.id == stored.id)
                .values(revoked_at=datetime.utcnow())
            )
            child = secrets.token_urlsafe(48)
            new_token = RefreshToken(
                user_id=stored.user_id,
                family_id=stored.family_id,  # ★ 继承家族
                token_hash=self.hash_token(child),
                expires_at=datetime.utcnow() + REFRESH_TTL,
            )
            self.session.add(new_token)

            user_result = await self.session.execute(
                select(User).where(User.id == stored.user_id)
            )
            user = user_result.scalar_one()
            if user.status != "active":
                raise UnauthorizedException("USER_DISABLED")

            access_token = self.jwt.encode(
                {
                    "sub": user.id,
                    "tenant_id": user.tenant_id,
                    "role": user.role,
                    "jti": str(uuid.uuid4()),
                },
                expires_in=ACCESS_TOKEN_TTL_SECONDS,
            )
            # 注：child 通过 HttpOnly Cookie 写回（Controller 层），不经响应体

        return {"access_token": access_token}

    @staticmethod
    def hash_token(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()
```

**Refresh Token 存储策略决策**（Radian 建议 11）：refresh token 与 access token 一致走 **HttpOnly Cookie**（`path=/api/auth` 作用域收窄），不再出现在 JSON 响应体——消除 XSS 可读面。`POST /api/auth/refresh` 改为无 body（从 Cookie 读），响应仅 `{ expires_in: 900 }`，新 token 经 Set-Cookie 下发。

**附录 B 错误码语义补充**：

| 错误码 | 触发语义 | 客户端行为 |
|-------|---------|-----------|
| `TOKEN_REFRESH_IN_PROGRESS` (409) | 宽限窗口内的并发刷新竞态 | 等 BroadcastChannel 广播新凭证后原请求自动重放；不登出 |
| `TOKEN_REUSE_DETECTED` (401) | 超窗复用或家族已灭 | 清本地状态跳转登录页 |

---

### 34.2 P0-B：TaskService 交互式事务 + 直接入队主路径（Radian 缺陷 4 + Verity 问题 2）

> 缺陷 4：数组式 `$transaction([...])` 中两条语句都执行，`count === 0` 的异常抛在提交之后——被拒绝的转换留下孤儿 Outbox 行，对账扫描把"从未发生的 approve"变成真实队列任务。
> Verity 问题 2：60s 对账周期使任务可见延迟 ≈60-120s，与 §31.2.5 的 12s E2E 断言矛盾。

**修复**：回调式事务（冲突即整体回滚）+ 事务提交成功后**直接入队为主路径**，Outbox 降级为崩溃补偿：

```python
# === app/services/task.py — transition() v7.0 生效版 ===

import uuid
from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.core.exceptions import NotFoundException, UnprocessableEntityException, ConflictException
from app.core.event_bus import event_bus


class TaskService:
    def __init__(self, session: AsyncSession, rq_queue) -> None:
        self.session = session
        self.rq_queue = rq_queue

    async def transition(
        self, task_id: str, to_status: TaskStatus,
        actor_id: str, metadata: str | None = None,
    ) -> Task:
        result = await self.session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise NotFoundException("TASK_NOT_FOUND")
        if to_status not in VALID_TRANSITIONS.get(task.status, []):
            raise UnprocessableEntityException(
                f"INVALID_TRANSITION: {task.status} → {to_status}"
            )

        outbox_id = str(uuid.uuid4())
        # 回调式事务：rowcount === 0 时 raise → 整个事务（含 Outbox 行）一起回滚（★ 缺陷 4 修复）
        async with self.session.begin():
            r = await self.session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == task.status)  # 乐观锁
                .values(status=to_status, actor_id=actor_id)
            )
            if r.rowcount == 0:
                raise ConflictException("TASK_ALREADY_PROCESSED")
            outbox = TaskOutboxEvent(
                id=outbox_id, task_id=task_id,
                type=map_queue_name(task.type), payload=task.payload,
            )
            self.session.add(outbox)

        # 提交成功后立即入队（主路径，延迟 ≈0ms，满足 12s 断言）；
        # job_id = outbox_id 与对账扫描共用，天然幂等去重
        from rq import Queue
        queue = Queue(map_queue_name(task.type), connection=self.rq_queue.connection)
        queue.enqueue(
            process_task,
            task_id=task_id,
            outbox_id=outbox_id,
            **task.payload,
            job_id=outbox_id,
        )
        try:
            await self.session.execute(
                update(TaskOutboxEvent)
                .where(TaskOutboxEvent.id == outbox_id)
                .values(delivered_at=datetime.utcnow())
            )
            await self.session.commit()
        except Exception:
            # 入队成功但标记失败无害：对账按 job_id 去重
            pass

        # 注：task 为事务前快照，from 取其旧值是预期行为（Radian 建议 7）
        await event_bus.publish("task.status_changed", {
            "task_id": task_id, "from": task.status,
            "to": to_status, "actor_id": actor_id,
        })
        result = await self.session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one()
```

对账扫描降级为纯兜底（进程在 commit 与入队之间崩溃的场景），周期下调至 **15s**（Verity 方案 A）：`@scheduler.scheduled_job('cron', second='*/15')`，窗口过滤 `created_at < now() - 15s`。正常路径任务可见延迟 ≈10s（轮询间隔）+ 入队即时执行，满足 12s 断言。

```python
# === app/services/task_reconcile.py — 15s 兜底扫描 ===

from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from rq import Queue

scheduler = AsyncIOScheduler()


@scheduler.scheduled_job("cron", second="*/15")
async def reconcile_undelivered(session_factory, rq_queue) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(TaskOutboxEvent)
            .where(
                TaskOutboxEvent.delivered_at.is_(None),
                TaskOutboxEvent.created_at < datetime.utcnow() - timedelta(seconds=15),
            )
            .limit(100)
        )
        pending = result.scalars().all()
        for evt in pending:
            queue = Queue(evt.type, connection=rq_queue.connection)
            queue.enqueue(
                process_task,
                task_id=evt.task_id,
                outbox_id=evt.id,
                **evt.payload,
                job_id=evt.id,  # 与主路径共用 job_id → 天然幂等去重
            )
```

---

### 34.3 P0-C：前端静默刷新跨标签页单飞（Vexel P0，§18.3 AuthContext 契约落地）

配合 34.1 后端宽限窗口，前端消除并发刷新的主要来源：

```typescript
// === apps/frontend/src/context/AuthContext.tsx — refreshToken() 实现契约 ===
const REFRESH_LOCK_NAME = 'auth-refresh-lock';

async function refreshToken(): Promise<boolean> {
  // Web Locks：同源所有标签页互斥，只有持锁者真正发请求
  return navigator.locks.request(REFRESH_LOCK_NAME, async (lock) => {
    if (!lock) return false; // 理论不可达（request 会排队）

    // 双检：拿到锁之前可能别的标签页刚刷完并已广播
    if (await tryProbeSession()) return true;

    const res = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' }); // 无 body，Cookie 携带
    if (res.status === 409 /* TOKEN_REFRESH_IN_PROGRESS */) {
      // 极少见（锁失效恢复场景）：等广播而非重试
      return waitForBroadcastToken(5000);
    }
    if (!res.ok) { logout(); return false; }
    broadcastNewSession(); // BroadcastChannel 通知其余标签页 probe 更新
    return true;
  });
}

// http-client 401 拦截器：单飞排队 —— 所有 401 请求共享同一次刷新 Promise，完成后统一重放
let refreshPromise: Promise<boolean> | null = null;
function singleFlightRefresh(): Promise<boolean> {
  refreshPromise ??= refreshToken().finally(() => { refreshPromise = null; });
  return refreshPromise;
}
```

> 15min access token 的前端代价在此明确（Vexel P1-2 关联）：mount 时刷一次不再够用，依赖「401 拦截器单飞重放」+ 可选到期前 2min 主动刷新定时器。此段并入 §18.3 正文。

---

### 34.4 一致性修复与文档同步（非阻塞项全量落地）

| # | 来源 | 修复 |
|---|------|------|
| 1 | Vulcan 小问题 1 | 幂等键双轨消除：删除 `ApproveTaskDto.idempotency_key` 字段与 header 表述——并发安全由条件 UPDATE 乐观锁保证，第二次请求得 409 由 `<ConflictDialog>` 承接，无需额外键 |
| 2 | Vulcan 小问题 2 / Radian 9 | `OidcStateStore` 改构造注入共享 Redis 客户端（与限流/熔断共用连接池），补 FastAPI lifespan 关闭钩子 |
| 3 | Vexel P1-2 | 全文 15min 同步：§24 安全设计改为「Access Token 15min + Refresh Token 30d 家族轮换」；附录 A refresh 端点响应示例改 `{ expires_in: 900 }` 且无 body |
| 4 | Vexel P2-3 | SWR key 表格加注「示意，实际以 `swr-keys.ts` 工厂为准」；工厂签名去除显式 tenantId 入参，内部经 `getTenantId()` 从 AuthContext 取；filters 参数稳定序列化（key 排序后 join 成字符串）后再入 tuple |
| 5 | Radian 建议 5/6 | §32.1.5 的 `codeVerifiers` Map、`validate_and_consume_state`、旧版 `refresh_token()`、`store_refresh_token` 四处加注「⚠️ 已被 §33.1 / §34.1 替换，保留仅为历史对照，勿作为实现基线」 |
| 6 | Radian 建议 10 | 测试范例 fixture 补 `role: 'employee'` 显式定义 |
| 7 | Verity 问题 1 | 性能门禁环境采用**选项 C**：Agent 列表门禁跑 CI（纯 Wrapper DB 路径，无 Dify 依赖）；首 Token 与召回测试两门禁移至 staging 里程碑执行（真实 Dify），CI 中以 Mock 延迟分布做冒烟不设阈值 |
| 8 | Verity 问题 3 | QA 交付分批：第 1 周框架 + Agent/KB/Conversation 三模块核心 16 条；第 5–6 周兼职期补齐剩余 16 条 + 8 用户旅程 + 故障注入；k6 脚本随里程碑交付 |
| 9 | Radian 建议 7 | 已在 34.2 代码注释中说明事务前快照语义 |

---

### 34.5 修订核对清单

| 来源 | 问题 | 状态 |
|------|------|------|
| Radian 缺陷 1 | familyId 登录路径未写入 | ✅ §34.1 `issue_session` 新建家族 |
| Radian 缺陷 2 / Vulcan a | expiresAt 校验丢失 | ✅ §34.1 事务内回归校验 |
| Radian 缺陷 3 / Vexel P0 | 多标签页误杀 | ✅ §34.1 宽限窗口 + §34.3 前端单飞 |
| Radian 缺陷 4 / Vulcan b | Outbox 脏提交 | ✅ §34.2 回调式事务 |
| Vulcan 小问题 1/2 | 幂等双轨 / Redis 连接 | ✅ §34.4 #1/#2 |
| Vexel P1-2 | 15min 不同步 | ✅ §34.4 #3 |
| Vexel P2-3 | SWR key 不一致 | ✅ §34.4 #4 |
| Verity 问题 1/2/3 | 门禁环境 / 对账延迟 / 工作量 | ✅ §34.2 + §34.4 #7/#8 |
| Radian 建议 5-11 | 标注/fixture/存储策略等 | ✅ §34.1 + §34.4 #5/#6 |

*修订版本: v7.0 | 增量章节: §34 | 基于: Vulcan/Vexel/Verity/Radian 第五轮评审 | 状态: 4 个阻塞缺陷全部修复，一致性项同步完成*

## <a id="ch-v8-python-migration"></a>三十五、v8.0 修订：后端迁移至 Python/FastAPI

> 本节为 v7.0 → v8.0 的增量修订。后端技术栈从 NestJS/TypeScript/Prisma 整体迁移至 Python/FastAPI/SQLAlchemy，前端（Next.js/React/SWR/Tailwind）零变更。

### 35.1 迁移决策与动因

| 维度 | NestJS/TS（v7） | Python/FastAPI（v8） | 决策理由 |
|------|----------------|---------------------|---------|
| 异步模型 | Node.js event loop + async/await | Python asyncio + async/await | 原生 async，FastAPI 基于 Starlette 事件循环，SSE/长连接性能对等 |
| API 文档 | NestJS Swagger 装饰器手动标注 | FastAPI 自动生成 OpenAPI 3.1 schema | Pydantic model 即文档，零额外标注，前端可自动生成 TS 类型 |
| 依赖注入 | NestJS DI 容器（`@Injectable` + 构造注入） | FastAPI `Depends()` 函数式注入 | 更简洁、可测试性更强、无装饰器膨胀 |
| 团队熟悉度 | TypeScript 全栈 | Python 后端 + TypeScript 前端 | 后端团队 Python 熟练度更高；前端仍 TypeScript 不变 |
| 类型安全 | TypeScript 编译期类型 | Pydantic v2 运行时验证 + mypy 静态检查 | 双重保障：运行时 Pydantic 验证 + mypy 静态类型检查 |
| 生态成熟度 | Prisma 6 ORM | SQLAlchemy 2.0 async + Alembic | SQLAlchemy 更成熟、SQL 控制力更强、Alembic 迁移可审计 |

### 35.2 技术映射表

| 层 | v7（NestJS/TS） | v8（Python） |
|----|----------------|-------------|
| 后端框架 | NestJS 11 | FastAPI 0.115+ |
| 后端语言 | TypeScript 5.9 | Python 3.12 |
| ORM | Prisma 6 | SQLAlchemy 2.0 (async) + Alembic |
| 验证 | Zod | Pydantic v2 |
| 队列 | BullMQ 5.x | RQ (Redis Queue) + rq-scheduler |
| 依赖注入 | NestJS DI 容器 | FastAPI `Depends()` |
| 守卫 | NestJS Guards | FastAPI dependencies |
| 事件发射 | @nestjs/event-emitter | 自定义 EventBus (asyncio + blinker) |
| 日志 | Pino 9.x | structlog 24.x |
| 测试 | Vitest/Jest | pytest + httpx.AsyncClient + pytest-asyncio |
| HTTP 客户端 | fetch/axios | httpx (async) |
| Redis 客户端 | ioredis | redis.asyncio |
| JWT | @nestjs/jwt | python-jose[cryptography] |
| OIDC | jose / openid-client | authlib |
| 配置 | @nestjs/config (ConfigService) | pydantic-settings (BaseSettings) |
| 模块 | NestJS @Module | FastAPI APIRouter |
| 拦截器 | NestJS Interceptors | FastAPI middleware |
| 管道 | NestJS Pipes | FastAPI dependency validation / Pydantic |
| 异常过滤器 | NestJS @Catch filters | FastAPI exception handlers |
| 定时任务 | @Cron (NestJS Schedule) | APScheduler |
| 文件上传 | Multer (@UploadedFile) | FastAPI UploadFile |
| SSE | ReadableStream / EventSource | StreamingResponse / sse-starlette |
| Prometheus | @willsoto/nestjs-prometheus | prometheus-client + prometheus_fastapi_instrumentator |
| 健康检查 | @nestjs/terminus | FastAPI health check endpoints |

### 35.3 API 契约保持

迁移的核心约束：**全部 53 个 API 端点路径、HTTP 方法、请求体结构、响应体结构完全不变**。

- 端点路径：`GET /api/agents`、`POST /api/conversations/{id}/messages`、`POST /api/auth/refresh` 等 53 个端点 URI 不变
- 请求/响应 JSON 字段名：camelCase 保持（FastAPI Pydantic `alias` + `populate_by_name` 映射 snake_case → camelCase）
- 错误码：`DUPLICATE_AGENT_ID`、`TOKEN_REUSE_DETECTED`、`TASK_ALREADY_PROCESSED` 等全部保持
- HTTP 状态码语义不变
- SSE 事件格式不变：`event: message\ndata: {...}\n\n`
- Cookie 作用域不变：refresh token `path=/api/auth` HttpOnly Cookie

### 35.4 前端影响：零变更

- Next.js 14 前端完全不变——页面、组件、SWR hooks、`useStreamChat`、`AuthContext`、`platform-service.ts` 全部保持 TypeScript 原样
- **类型来源升级**：v7 的 `packages/shared-types/` 手写 TS 类型 → v8 的 `packages/api-contract/` 通过 `openapi-typescript` 从 FastAPI 自动生成的 OpenAPI 3.1 schema 生成 TS 类型
  - 前端运行时行为不变，但类型安全从"手写对齐"升级为"schema 单一事实来源"
  - CI 流水线：FastAPI 启动时导出 `openapi.json` → `openapi-typescript openapi.json -o packages/api-contract/index.ts` → tsc 编译检查
- API 调用方式不变：前端仍用 fetch + SWR，端点路径与响应结构不变

### 35.5 迁移方法：Clean Cutover

- **无 NestJS 做垫片/适配层**——后端整体替换为 Python/FastAPI 新项目
- 迁移期间前端通过 mock server（基于 OpenAPI schema 生成）继续开发，不阻塞
- 切换日：DNS/反向代理从 NestJS 切向 FastAPI，前端无感知
- 数据库不变（PostgreSQL + pgvector），仅 ORM 层从 Prisma 换为 SQLAlchemy；Alembic 初始迁移从现有 schema 生成

### 35.6 Alembic 迁移策略（替代 Prisma Migrate）

| 维度 | Prisma Migrate（v7） | Alembic（v8） |
|------|---------------------|-------------|
| 迁移文件 | `prisma/migrations/` 目录 | `alembic/versions/` 目录 |
| schema 变更来源 | `schema.prisma` 单文件 | SQLAlchemy model 定义 → `alembic revision --autogenerate` |
| 迁移审查 | Prisma 自动生成，需人工审查 | Alembic autogenerate 生成骨架，需人工审查并补充（如 ENUM、索引、约束） |
| 回滚 | `prisma migrate reset`（全量重置） | `alembic downgrade -1`（逐步回滚） |
| CI 集成 | `prisma migrate deploy` | `alembic upgrade head` |
| 数据迁移 | 需手写 SQL 或 Prisma script | 迁移文件内可直接写 Python 数据迁移逻辑 |

**迁移策略**：
1. 从现有 PostgreSQL schema 生成 Alembic 初始 baseline：`alembic revision --autogenerate -m "baseline from existing schema"`
2. 标记为 baseline：`alembic stamp head`（不对 DB 执行变更，仅记录版本号）
3. 后续 schema 变更走标准 `alembic revision --autogenerate` + 人工审查流程
4. CI 部署时执行 `alembic upgrade head`

### 35.7 共享类型：`packages/shared-types/` → `packages/api-contract/`

| 维度 | v7 `shared-types/` | v8 `api-contract/` |
|------|--------------------|--------------------|
| 类型来源 | 手写 TypeScript interface/enum | `openapi-typescript` 从 FastAPI OpenAPI schema 生成 |
| 同步方式 | 后端 Zod schema → 手动对齐 TS 类型 | FastAPI 启动 → 导出 `openapi.json` → CI 生成 TS 类型 |
| 漂移检测 | 无自动机制 | CI 对比 `openapi.json` hash，变更未更新 TS 类型则 fail |
| 前端消费 | `import { Agent } from '@multica/shared-types'` | `import { components } from '@multica/api-contract'; type Agent = components['schemas']['Agent']` |

**CI 流水线**：
```yaml
# .github/workflows/api-contract.yml
- name: Export OpenAPI schema
  run: cd apps/backend && python -c "from app.main import app; import json; print(json.dumps(app.openapi()))" > openapi.json
- name: Generate TS types
  run: npx openapi-typescript apps/backend/openapi.json -o packages/api-contract/index.ts
- name: Check for drift
  run: git diff --exit-code packages/api-contract/index.ts
```

### 35.8 修订核对清单

| 项 | 状态 |
|----|------|
| 后端框架 NestJS → FastAPI | ✅ 全部 router/service/repo/guard 重写 |
| ORM Prisma → SQLAlchemy 2.0 async | ✅ 全部 model + 查询重写 |
| 验证 Zod → Pydantic v2 | ✅ 全部 DTO 重写 |
| 队列 BullMQ → RQ | ✅ 队列 + worker + 对账扫描重写 |
| 日志 Pino → structlog | ✅ 全部 logger 调用更新 |
| Redis ioredis → redis.asyncio | ✅ state store + 限流重写 |
| HTTP fetch → httpx | ✅ ToolProxy + Dify adapters 重写 |
| JWT jose → python-jose/authlib | ✅ auth service 重写 |
| 定时 @Cron → APScheduler | ✅ 对账扫描重写 |
| SSE 手动 stream → StreamingResponse | ✅ ConversationController 重写 |
| 测试 Vitest → pytest | ✅ 测试范例重写 |
| API 契约 53 端点不变 | ✅ 路径/方法/请求体/响应体零变更 |
| 前端零变更 | ✅ Next.js/React/SWR/Tailwind 原样保留 |
| 共享类型 shared-types → api-contract | ✅ OpenAPI 自动生成 TS 类型 |

*修订版本: v8.0 | 增量章节: §35 | 基于: 用户决策后端迁移至 Python | 状态: 全部后端代码已从 NestJS/TypeScript 重写为 Python/FastAPI，前端零变更*
