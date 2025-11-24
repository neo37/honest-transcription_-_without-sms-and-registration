.PHONY: help build up down test test-report clean migrate

help:
	@echo "Доступные команды:"
	@echo "  make build       - Собрать Docker образы"
	@echo "  make up          - Запустить приложение"
	@echo "  make down        - Остановить приложение"
	@echo "  make test        - Запустить тесты"
	@echo "  make test-report - Запустить тесты и показать отчёт"
	@echo "  make migrate     - Выполнить миграции"
	@echo "  make clean       - Очистить временные файлы"

build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

test:
	docker-compose run --rm test

test-report:
	./run_tests.sh

migrate:
	docker-compose run --rm web python manage.py migrate

clean:
	rm -rf test-results test-reports htmlcov .pytest_cache .coverage
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete


