# 前端验收用例（TC-tech-*）

设计仓 `docs/03-acceptance.md` 的每条前端技术用例在实现仓有唯一可执行形态：模块名对应用例标识（`TC-tech-ui-01` → `test_tc_tech_ui_01.test.tsx`），模块内 `TC_ID` 与标识一致，断言对应该用例的可观察判据。

| 标识 | AC | 模块 |
|---|---|---|
| `TC-tech-ui-01` | AC-T12 | `test_tc_tech_ui_01.test.tsx` |

## 按标识执行

```bash
cd apps/frontend
pnpm acceptance                  # 全部前端 TC-tech-* 用例
pnpm acceptance TC-tech-ui-01    # 单条按标识执行
pnpm acceptance --list           # 列出可用标识
```

根目录等价入口：`make acceptance-ui`（单条：`make acceptance-ui TC=TC-tech-ui-01`）。

## 取证边界

- `TC-tech-ui-01`：槽位取值在运行时从 `app/globals.css` 读取并复算（未取整，C-84）。class 清单扫描基于 TypeScript AST，建模跨元素继承（元素的语义前景对最近祖先的语义背景；条件分支保持为互斥备选），覆盖语义色 pair 的声明完整性。**本套件不取证渲染后的计算样式或像素值**；e2e 的 axe 扫描是独立信号，不承担 pair 声明仲裁。字号由 Tailwind 默认刻度断言（`text-xs` = 0.75rem），非布局后实测。
- `TC-tech-ops-02` 末条「浏览器请求实际落在同源相对路径」的浏览器侧证据在 `e2e/same-origin.spec.ts`，随 e2e 套件执行（单跑：`pnpm test:e2e e2e/same-origin.spec.ts`）；后端模块已注明该取证边界。

## 已知实现缺口

`declared-pairs.ts` 的 `KNOWN_GAPS` 记录当前基线下未达阈值的 pair（对应后端套件的 `xfail(strict=False)` 约定）。它们仍逐条复算，不跳过；修复落地后删除对应条目。当前为空：全部已声明 pair 均达对应文本档阈值。
