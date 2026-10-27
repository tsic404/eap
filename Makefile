.PHONY: setup-env qa-up qa-down qa-keys acceptance acceptance-ui e2e

# 后端验收用例（设计 docs/03-acceptance.md 的 TC-tech-* 标识）。
# 全部用例：make acceptance；单条：make acceptance TC=TC-tech-kb-01
TC :=

acceptance:
	cd apps/backend && uv run python -m tests.acceptance $(TC)

# 前端验收用例（TC-tech-ui-01、TC-kb-06、TC-sec-02；浏览器侧证据在 pnpm test:e2e）。
# 全部前端用例：make acceptance-ui；单条：make acceptance-ui TC=TC-kb-06
acceptance-ui:
	cd apps/frontend && pnpm acceptance $(TC)

QA_COMPOSE := docker-compose.qa.yml

setup-env:
	@./scripts/setup-env.sh
	@./scripts/gen-jwt-keys.sh

# QA 环境（SSO 端到端）：生成 JWT 密钥 + 构建并启动全部服务。
# 入口 http://localhost:8090，详见 README「QA 环境」一节。
qa-keys:
	@./scripts/gen-jwt-keys.sh deploy/qa/keys

qa-up: qa-keys
	docker compose -f $(QA_COMPOSE) up -d --build
	@echo ""
	@echo "QA environment: http://localhost:8090"
	@echo "Follow logs:    docker compose -f $(QA_COMPOSE) logs -f backend"

qa-down:
	docker compose -f $(QA_COMPOSE) down

# 容器内 e2e：宿主只需 docker（无需 pnpm、可执行浏览器或后端 venv）。
# 默认跑 e2e/journeys/tc-auth-08-local-login.spec.ts；可换其它只依赖 QA 栈种子
# 的自足 journey：
#   make e2e E2E_SPEC=e2e/journeys/tc-auth-09-...spec.ts
# 完整套件仍走 pnpm --filter @eap/frontend test:e2e（需要宿主工具链与 global-setup）。
# 报告：artifacts/playwright/report/index.html
e2e:
	@./scripts/e2e.sh
