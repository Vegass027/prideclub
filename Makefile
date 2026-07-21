.PHONY: dev down logs logs-backend shell-backend restart-backend \
        test lint format \
        migrate migrate-test migrate-new \
        backup backup-test \
        seed seed-club \
        deploy logs-prod status health-check help

help:           ## Показать эту справку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Разработка ===

dev:            ## Поднять всё окружение (postgres, redis, backend, bot, worker)
	docker-compose up -d postgres redis
	sleep 3
	docker-compose up -d backend bot worker
	@echo "✓ Окружение поднято. Backend: http://localhost:8000"

down:           ## Остановить всё окружение
	docker-compose down

logs:           ## Логи всех сервисов
	docker-compose logs -f

logs-backend:   ## Только логи backend
	docker-compose logs -f backend

shell-backend:  ## Зайти в контейнер backend
	docker-compose exec backend bash

restart-backend: ## Перезапустить backend
	docker-compose restart backend

# === Тесты и линт ===

test:           ## Прогнать pytest (unit + integration)
	docker-compose exec backend pytest -v

lint:           ## ruff + mypy
	docker-compose exec backend ruff check .
	docker-compose exec backend mypy app/

format:         ## black + ruff --fix
	docker-compose exec backend black .
	docker-compose exec backend ruff check --fix .

# === Миграции и бэкапы ===

migrate:        ## Применить миграции
	docker-compose exec backend alembic upgrade head

migrate-test:   ## Тест миграций: upgrade → downgrade → upgrade
	docker-compose exec backend bash -c "alembic upgrade head && alembic downgrade base && alembic upgrade head"
	@echo "✓ Миграции прошли round-trip"

migrate-new:    ## Создать новую миграцию (сообщение: make migrate-new m="add field")
	docker-compose exec backend alembic revision --autogenerate -m "$(m)"

backup:         ## Создать бэкап вручную
	./infra/backup/backup_cron.sh

backup-test:    ## Тестовый restore из последнего бэкапа
	./infra/backup/restore_test.sh

# === Seed-данные ===

seed:           ## Создать тестовый клуб + admin-юзера + 3 фейковых участников
	docker compose exec backend python -m scripts.seed_dev_data

seed-club:      ## Создать только новый клуб (id=$HABIT_ID, name=$HABIT_NAME)
	docker compose exec backend python -m scripts.create_club habit_id=$(HABIT_ID) name="$(HABIT_NAME)"

health-check:   ## Проверить /health и /ready всех сервисов
	@echo "=== backend ===" && curl -fsS http://localhost:8000/health || echo "FAIL"
	@echo "=== backend ready ===" && curl -fsS http://localhost:8000/ready || echo "FAIL"
	@echo "=== frontend ===" && curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:5173/

# === Деплой ===

deploy:         ## Деплой на VPS (требует SSH_KEY и SERVER)
	./infra/deploy.sh $(SERVER) $(SSH_KEY)

setup-server:   ## Подготовить Ubuntu 24.04 VPS (один раз перед первым деплоем)
	ssh -i $(SSH_KEY) $(SERVER) 'bash -s' < infra/setup_server.sh

logs-prod:      ## Логи с прода (ssh tail)
	ssh -i $(SSH_KEY) $(SERVER) "cd /app && docker-compose logs -f --tail=100"

status:         ## Статус сервисов на проде
	ssh -i $(SSH_KEY) $(SERVER) "cd /app && docker-compose ps"
