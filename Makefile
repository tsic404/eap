.PHONY: setup-env qa-up qa-down qa-keys

QA_COMPOSE := docker-compose.qa.yml

setup-env:
	@./scripts/setup-env.sh

# QA 环境（SSO 端到端）：生成 JWT 密钥 + 构建并启动全部服务。
# 入口 http://localhost:8090，详见 README「QA 环境」一节。
qa-keys:
	@./scripts/qa-gen-keys.sh

qa-up: qa-keys
	docker compose -f $(QA_COMPOSE) up -d --build
	@echo ""
	@echo "QA environment: http://localhost:8090"
	@echo "Follow logs:    docker compose -f $(QA_COMPOSE) logs -f backend"

qa-down:
	docker compose -f $(QA_COMPOSE) down
