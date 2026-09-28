.PHONY: up down test migrate seed run worker beat lint
up:        ## start everything (api, worker, beat, postgres, redis)
	docker compose up --build
down:
	docker compose down -v
migrate:   ## apply DB migrations
	alembic upgrade head
seed:      ## admin user + demo catalogue
	python -m app.seed
run:
	uvicorn app.main:app --reload
worker:
	celery -A app.worker.celery_app worker -l info
beat:
	celery -A app.worker.celery_app beat -l info
test:      ## Redis / Postgres integration tests auto-skip unless REDIS_TEST_URL / MIGRATION_TEST_URL are set
	pytest
