#!/usr/bin/env bash
# Загрузить дамп в chemico_db, предварительно пересоздав БД.
#
# Использование:
#   ./chemico_agent/load_dump.sh <path-to-dump.sql.gz>
#
# Пример:
#   ./chemico_agent/load_dump.sh pg_mcp_export-import-assasin/downloaded_slivki_data/2026-03-13_03-15__chemico__.sql.gz
#
# Что делает:
#   1. Останавливает chemico_db
#   2. Удаляет volume (все данные)
#   3. Поднимает chemico_db заново
#   4. Заливает дамп (.sql.gz или .sql)

set -euo pipefail

DUMP_FILE="${1:-}"
if [[ -z "$DUMP_FILE" ]]; then
  echo "Использование: $0 <путь-к-дампу.sql.gz>"
  echo ""
  echo "Доступные chemico-дампы:"
  ls pg_mcp_export-import-assasin/downloaded_slivki_data/*chemico* 2>/dev/null || echo "  (нет)"
  exit 1
fi

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "Ошибка: файл не найден: $DUMP_FILE"
  exit 1
fi

echo "=== Дамп: $DUMP_FILE ==="
echo "=== 1. Останавливаем chemico_db ==="
docker compose stop chemico_db

echo "=== 2. Удаляем volume meetrec_chemico_pgdata ==="
docker compose rm -f chemico_db
docker volume rm meetrec_chemico_pgdata 2>/dev/null || true

echo "=== 3. Поднимаем чистый chemico_db ==="
docker compose up -d chemico_db

echo "    Ожидаем готовности PostgreSQL..."
until docker compose exec chemico_db pg_isready -U chemico -d chemico -q; do
  sleep 1
done
echo "    PostgreSQL готов."

echo "=== 4. Загружаем дамп... ==="
START_TIME=$(date +%s)

if [[ "$DUMP_FILE" == *.gz ]]; then
  gunzip -c "$DUMP_FILE" | docker compose exec -T chemico_db psql -U chemico -d chemico --single-transaction -q
else
  docker compose exec -T chemico_db psql -U chemico -d chemico --single-transaction -q < "$DUMP_FILE"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "=== Готово за ${ELAPSED}с ==="
TABLE_COUNT=$(docker compose exec chemico_db psql -U chemico -d chemico -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
echo "Таблиц в БД: $TABLE_COUNT"
echo ""
echo "Строка подключения:"
echo "  postgresql://chemico:chemico@chemico_db:5432/chemico"
