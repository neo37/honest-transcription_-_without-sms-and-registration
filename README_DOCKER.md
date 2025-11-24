# Docker Compose Setup

## Быстрый старт

### Запуск приложения

```bash
# Собрать образы
make build
# или
docker-compose build

# Запустить приложение
make up
# или
docker-compose up -d

# Приложение будет доступно на http://localhost:8000
```

### Запуск тестов

```bash
# Запустить все тесты с отчётом
make test-report
# или
./scripts/run_tests.sh

# Или просто запустить тесты
make test
# или
docker-compose run --rm test
```

### Остановка

```bash
make down
# или
docker-compose down
```

## Структура тестов

Тесты организованы в директории `transcribe/tests/`:

- `test_models.py` - тесты моделей
- `test_views.py` - сценарные тесты views
- `test_utils.py` - тесты утилит
- `test_integration.py` - интеграционные тесты

## Отчёты

После запуска тестов отчёты будут доступны в:

- **HTML отчёт**: `test-reports/report.html`
- **Покрытие кода**: `test-reports/coverage/index.html`
- **JUnit XML**: `test-results/junit.xml`

## Команды Make

- `make build` - собрать Docker образы
- `make up` - запустить приложение
- `make down` - остановить приложение
- `make test` - запустить тесты
- `make test-report` - запустить тесты и показать отчёт
- `make migrate` - выполнить миграции БД
- `make clean` - очистить временные файлы

## Переменные окружения

Можно настроить через `.env` файл или в `docker-compose.yml`:

- `DEBUG` - режим отладки (True/False)
- `SECRET_KEY` - секретный ключ Django
- `ALLOWED_HOSTS` - разрешённые хосты

## Разработка

Для разработки можно монтировать код:

```bash
docker-compose up -d
# Код монтируется автоматически через volumes
```

Изменения в коде будут видны сразу (кроме изменений в requirements).

## Миграции

```bash
make migrate
# или
docker-compose run --rm web python manage.py migrate
```

## Логи

```bash
# Просмотр логов приложения
docker-compose logs -f web

# Просмотр логов тестов
docker-compose logs test
```


