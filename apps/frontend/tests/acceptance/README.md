# 前端验收用例（TC-*）

设计仓 `docs/03-acceptance.md` 的每条前端用例在实现仓有唯一可执行形态：模块名对应用例标识（`TC-tech-ui-01` → `test_tc_tech_ui_01.test.tsx`，`TC-kb-06` → `test_tc_kb_06.test.tsx`），模块内 `TC_ID` 与标识一致，断言对应该用例的可观察判据。

| 标识 | AC | 模块 |
|---|---|---|
| `TC-tech-ui-01` | AC-T12 | `test_tc_tech_ui_01.test.tsx` |
| `TC-kb-01` | AC-15 | `test_tc_kb_01.test.tsx` |
| `TC-kb-06` | AC-20 | `test_tc_kb_06.test.tsx` |
| `TC-agent-04` | AC-11 | `test_tc_agent_04.test.tsx` |
| `TC-agent-05` | AC-12 | `test_tc_agent_05.test.tsx` |
| `TC-sec-02` | AC-43 | `test_tc_sec_02.test.tsx` |
| `TC-tool-05` | AC-34 | `test_tc_tool_05.test.tsx` |
| `TC-auth-02` | AC-02 | `test_tc_auth_02.test.tsx` |
| `TC-agent-01` | AC-08 | `test_tc_agent_01.test.tsx` |
| `TC-agent-06` | AC-13 | `test_tc_agent_06.test.tsx` |

## 按标识执行

```bash
cd apps/frontend
pnpm acceptance                  # 全部前端 TC-* 用例
pnpm acceptance TC-kb-06         # 单条按标识执行
pnpm acceptance --list           # 列出可用标识
```

根目录等价入口：`make acceptance-ui`（单条：`make acceptance-ui TC=TC-kb-06`）。

## 取证边界

- `TC-tech-ui-01`：槽位取值在运行时从 `app/globals.css` 读取并复算（未取整，C-84）。class 清单与行内 `style` 扫描基于 TypeScript AST，建模跨元素继承（元素的语义前景对最近祖先的语义背景；条件分支保持为互斥备选），覆盖语义色 pair 的声明完整性。行内色值须写成 `var(--color-<槽位>[, <取值>])`：`<取值>` 是该槽位当前取值的副本，必须逐字相等（防止令牌改名后副本静默漂移），单独出现的颜色字面量即判未声明。**本套件不取证渲染后的计算样式或像素值**；带色 SVG 属性（`stroke` / `fill`）与动态拼接的行内色值不在静态模型内；e2e 的 axe 扫描是独立信号，不承担 pair 声明仲裁。字号由 Tailwind 默认刻度断言（`text-xs` = 0.75rem），非布局后实测。
- `TC-kb-01`：创建知识库界面用例。表单与列表驱动真实 SWR 数据层与服务层，仅桩掉共享 axios 实例，断言成功提示、新卡片六要素（名称、描述、类型、文档数、分块数、状态徽章）与重名就地提示；浏览器侧证据在 `e2e/journeys/tc-kb-01-create.spec.ts`（同样的判据在真实 Chromium 中重放），随 e2e 套件执行（单跑：`pnpm test:e2e e2e/journeys/tc-kb-01-create.spec.ts`）。
- `TC-kb-06`：召回测试界面用例。表单驱动真实 `retrievalTest` 服务，仅桩掉共享 axios 实例，断言界面发出的 `top_k`（5→10）、结果卡片的渲染顺序与无关查询的空状态。上游排序属 `TC-tech-kb-03` 的证据，分数配色与 12px 文本对比度属 `TC-tech-ui-01`，均不在此重复判定；jsdom 不取渲染后的计算样式或像素值。浏览器侧证据在 `e2e/journeys/tc-kb-06-retrieval.spec.ts`（同样的判据在真实 Chromium 中重放，含滑块键盘调节），随 e2e 套件执行（单跑：`pnpm test:e2e e2e/journeys/tc-kb-06-retrieval.spec.ts`）。
- `TC-auth-02`：会话恢复。刷新通道的网络边界（`fetch`）与共享 axios 实例的适配器被桩成可判定的应答，断言的是各应答之后页面落在哪里（工作区内容留存 / 落到登录页）与刷新被调用的次数是否有界；jsdom 不执行导航，「进入工作区」读自页面发起的路由跳转。浏览器侧证据在 `e2e/journeys/tc-auth-02-session-resume.spec.ts`（真实 Chromium 中重放：引导期硬导航不常驻「正在完成登录…」、刷新不出现 409、已登录访问登录页直接进工作区），单跑：`pnpm test:e2e e2e/journeys/tc-auth-02-session-resume.spec.ts`。
- `TC-agent-04`：智能体详情页调试页签。模块驱动真实面板与流读取逻辑，仅桩掉 `fetch`，断言三页签切换（`aria-selected`）、用户气泡先于回答出现、回答以递增前缀逐字渲染、结束事件后轨迹标识可复制（剪贴板写入其取值），以及准备阶段失败的就地提示；上游模型不在桩内。浏览器侧证据在 `e2e/journeys/tc-agent-04-agent-debug.spec.ts`，须在配置 Dify workspace provider（`DIFY_*`）的完整栈上执行（`docker compose up -d --build` 后 `make e2e E2E_SPEC=e2e/journeys/tc-agent-04-agent-debug.spec.ts`），且租户内须存在被测的 `bot-1` 智能体。只有「无 Dify workspace provider」（`/api/health/ready` 的 `dify` 非 `ok`）可以让该用例自报 skip；`bot-1` 是否存在由页面自身的鉴权请求证明，缺失即判失败，不会静默跳过。前置成立时断言全量执行。
- `TC-sec-02`：空态引导。三个管理端列表的列表读取被桩成空页，断言呈现带可操作入口的引导卡片、且不渲染具名列表（`list` 角色）占位；同一模块再以非空数据确认该具名列表确实出现，使空态断言具备判别力。只读角色（审计员、知识库管理员）在同一引导卡片上没有创建入口，头部也没有新建按钮；智能体卡片是可用键盘进入的详情链接。员工端广场文案不在本用例。浏览器侧证据在 `e2e/journeys/tc-sec-02-empty-guidance.spec.ts`（真实 Chromium 中重放，含空态、键盘进入详情、`/admin/agents` 的 200 与角色门禁），单跑：`pnpm test:e2e e2e/journeys/tc-sec-02-empty-guidance.spec.ts`。
- `TC-tech-ops-02` 末条「浏览器请求实际落在同源相对路径」的浏览器侧证据在 `e2e/same-origin.spec.ts`，随 e2e 套件执行（单跑：`pnpm test:e2e e2e/same-origin.spec.ts`）；后端模块已注明该取证边界。
- `TC-conv-03`（AC-24）的删除撤销判据是界面用例、不在本套件（本套件按标识承载前端用例），其浏览器侧证据在 `e2e/journeys/conversation-delete-undo.spec.ts`，随 e2e 套件执行（单跑：`pnpm test:e2e e2e/journeys/conversation-delete-undo.spec.ts`）。
- `TC-agent-01`：分步创建向导。向导、步骤指示器、绑定分组与预览表是真实组件，仅桩掉工具/知识库列表与创建调用，断言的是四步命名与当前步标记、第三步同时提供知识库与工具绑定、预览逐项回显实际配置（含知识库名与 Prompt），以及创建成功后落入详情页；后端 201 属后端套件，浏览器侧证据在 `e2e/journeys/tc-agent-01-agent-create-wizard.spec.ts`。
- `TC-agent-06`：删除二次确认（AC-13）的 jsdom 证据驱动真实详情页、弹层与名称比较，桩掉的是数据来源与删除调用；浏览器侧证据在 `e2e/journeys/tc-agent-06-agent-delete-confirm.spec.ts`，以桩响应携带关联会话数量重放同一判据。上游删除语义（204 / 软删除 / 历史会话保留）属后端证据，不在此重复判定。
- `TC-agent-05`：生命周期徽章与入口。详情页、徽章与动作入口是真实组件，`useAgent` 走真实 SWR 与服务层，仅桩掉共享 axios 实例；断言四态徽章的文案与语义色族（`bg-muted` / `bg-info-subtle` / `bg-success-subtle` / `bg-danger-subtle`）、草稿点「开始测试」不弹确认且以当前版本发出请求、成功后徽章转测试中，以及已下线不存在可操作的发布入口。实际渲染色调（hue/saturation）与员工广场可见性属浏览器与外后端证据，分别在 `e2e/journeys/tc-agent-05-agent-lifecycle.spec.ts` 与后端套件，不在此重复判定。

## 已知实现缺口

`declared-pairs.ts` 的 `KNOWN_GAPS` 记录当前基线下未达阈值的 pair（对应后端套件的 `xfail(strict=False)` 约定）。它们仍逐条复算，不跳过；修复落地后删除对应条目。当前为空：全部已声明 pair 均达对应文本档阈值。
