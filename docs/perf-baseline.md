# P1 性能基线（k6）

两条 HTTP 级性能基线，对应 T25-FE 验收标准中的 k6 里程碑。阈值以 `thresholds`
内置在脚本里，`p(95)` 不达标即非零退出。

`agent-list.js` 的**权威运行在 CI 固定资源 runner**（`.github/workflows/ci.yml`
的 `k6` job，经 `scripts/k6-perf-gate.sh` 起后端 + seed + 发 token 后跑 k6）；
**本地运行仅供调参**——共享开发宿主机上其他任务的 CPU 争抢会整体抬升延迟分布
（实测 load 16+ 时 p(90) 达 511ms），本地门禁结果不可复现。

| 脚本 | 端点 | 门禁 | 依赖 |
| --- | --- | --- | --- |
| `k6/agent-list.js` | `GET /api/agents` | P95 < 330ms（CI 固定资源） | 后端 + PostgreSQL |
| `k6/first-token.js` | `POST /api/conversations/{id}/messages`（SSE） | P95 < 1500ms | 后端 + Dify + 已绑定智能体 |

## 前置条件

1. **k6 ≥ 0.40**：`response.status` 在该版本起为 number（脚本用数字字面量比较）。
   安装：<https://grafana.com/docs/k6/latest/set-up/install-k6/>。
2. **后端就绪**：`curl http://localhost/api/health/live` 返回 200。
3. **Dify 就绪（仅 first-token）**：`curl http://localhost/api/health/ready` 返回
   200（`/ready` 会探测 PostgreSQL / Redis / Dify，任一不可达返回 503）。
4. **dify_api_key 注入（仅 first-token）**：目标智能体已在后端绑定其 Dify 应用
   密钥（`dify_api_key` 存于后端，不注入 k6 脚本）。未绑定时消息流会以 Dify
   侧错误返回，first-token 样本不可信。
5. **身份与对象 ID**：`ACCESS_TOKEN`（OIDC 登录后经 `/api/auth/refresh` 获得），
   以及 `first-token` 所需的 `CONVERSATION_ID` / `AGENT_ID`（一个已存在会话及
   其绑定的智能体）。

## 运行

```bash
# Agent 列表（权威：CI `k6` job，固定资源 runner）
bash scripts/k6-perf-gate.sh    # 本地复现 CI 步骤（调参用，非权威）

# Agent 列表（本地调参：已有后端时直接跑脚本）
k6 run k6/agent-list.js -e ACCESS_TOKEN=... [-e BASE_URL=http://localhost/api]

# 首 Token（先确认 /api/health/ready 返回 200，智能体已绑定 Dify 密钥）
k6 run k6/first-token.js \
  -e ACCESS_TOKEN=... \
  -e CONVERSATION_ID=... \
  -e AGENT_ID=... \
  [-e BASE_URL=http://localhost/api]
```

`first-token.js` 的 `setup()` 会先探测 `/api/health/ready`，后端/Dify 未就绪时
直接报错退出，不会带着无效样本跑完。

## 已知限制

- **TTFB 代理**：k6 会缓冲完整响应体，无法单独观察 SSE 流的首个 `data:` 帧，
  故用 `response.timings.waiting`（首字节）近似首 Token 时延。
- **会话限流**：`POST /api/conversations/{id}/messages` 每用户 20 req/min，
  `first-token.js` 以单 VU + `sleep(3)` 节流（含请求耗时约 13–20 req/min），
  保持在门限内。
