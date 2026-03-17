"""
База знаний для Chemico DB агента — три режима для экспериментов.

Режим управляется через SystemConfig.chemico_kb_mode:

  0 — без Wiki (только БД, агент работает на своих знаниях о SQL и схеме)
  1 — Wiki Chemico (статья chemico-db-schema: описание таблиц, примеры SQL)
  2 — Wiki Chemico + дополнительный раздел Wiki (slug задаётся в chemico_kb_extra_slug)

Цель: эксперименты с качеством ответов в зависимости от контекста.

Переключение из бота: /db_mode 0 | /db_mode 1 | /db_mode 2
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# --- TTL cache for schema context (avoids DB hit on every Excel chat request) ---
_schema_cache: dict = {}
_SCHEMA_CACHE_TTL = 3600  # 1 hour


def get_wiki_context_cached() -> str:
    """Cached version of get_wiki_context() — refreshes at most once per hour."""
    now = time.time()
    if _schema_cache and now - _schema_cache.get('ts', 0) < _SCHEMA_CACHE_TTL:
        return _schema_cache['context']
    ctx = get_wiki_context()
    _schema_cache['context'] = ctx
    _schema_cache['ts'] = now
    logger.info('chemico_agent KB: schema cache refreshed (%d chars)', len(ctx))
    return ctx


def invalidate_schema_cache() -> None:
    """Force-invalidate the schema cache (e.g. after wiki article update)."""
    _schema_cache.clear()

WIKI_SLUG = 'chemico-db-schema'

# Встроенная документация — используется в режиме 1 как fallback если Wiki не создана.
# Содержит РЕАЛЬНУЮ схему таблиц (проверено через \d в psql).
# Обновляется командой: docker compose exec web python manage.py create_chemico_wiki
BUILTIN_SCHEMA_DOCS = """# Chemico — Схема базы данных BusinessPad

---

## ❌ КРИТИЧЕСКИЕ ЗАПРЕТЫ (нарушение = синтаксическая/логическая ошибка)

❌ `fop.deal_id` — поля НЕ СУЩЕСТВУЕТ в financialoperation!
   ✅ связь ТОЛЬКО через M2M: deal_deal_financial_operations.financialoperation_id

❌ `fo.deal_id` — то же самое, поля нет
   ✅ JOIN deal_deal_financial_operations dfo ON dfo.financialoperation_id = fo.id

❌ JOIN deal_company ON deal_company.draggable_ptr_id = fo.counterparty
   ✅ fo.counterparty — VARCHAR(250), НЕ FK, нельзя джойнить

❌ WHERE d.deal_number = 'К5-25'  — deal_number это INTEGER!
   ✅ WHERE d.registration_number = 'К5-25'  — registration_number это VARCHAR

❌ base_financial_operation.type = 'Закупка' или 'Продажа'  — таких значений нет!
   ✅ type = 'FOR_STANDARD' (обычные) или 'FOR_COMPUTED' (вычисляемые комиссии)

❌ productunit.type = 'Закупка' / 'Продажа'
   ✅ productunit.type = 'PURCHASED' (закупка) или 'SOLD' (продажа)

❌ financialoperation.operation_type = 'Закупка'
   ✅ operation_type = 'EXPENDITURE' (расход) или 'PROFITABLE' (доход)

❌ SQL-комментарии-заглушки внутри IN():  WHERE x IN ('К5', -- добавь ещё... )
   ✅ Только реальные значения: WHERE x IN ('К5-25', 'К6-25')

---

## Нестандартные первичные ключи

- **deal_deal.draggable_ptr_id** — PK сделки (НЕ id!)
- **deal_company.draggable_ptr_id** — PK компании (НЕ id!)
- **deal_dealstatus.draggablestatus_ptr_id** — PK статуса
- Имя статуса: через bp_draggablestatus.name (JOIN по id = draggablestatus_ptr_id)

---

## Уровни данных — ВАЖНО для Excel-обогащения

**УРОВЕНЬ СДЕЛКИ** (один набор данных на всю сделку, независимо от числа финопераций):
- Товар → deal_profit_calculator_product.name через productunit WHERE type='SOLD'
- Количество → productunit.planned_amount WHERE type='SOLD'
- Единица измерения → deal_profit_calculator_measurementunit.name через productunit(SOLD)
- Грузополучатель/Покупатель → deal_company.name через productunit.company_unit_id (SOLD)
- Производитель → deal_company.name через productunit.manufacturer_company_id (SOLD)
- Поставщик/Грузоотправитель → deal_company.name через providerunit.shipper_company_id
- Ключ: deal_deal.registration_number (например 'К5-25')

**УРОВЕНЬ ФИНОПЕРАЦИИ** (своя строка для каждой операции):
- Тип операции → base_financial_operation.name (текст: 'Покупка товара/услуги', 'Таможенная пошлина' и т.п.)
- Сумма → financialoperation.amount
- Валюта → currencyunit.name
- Банк/Канал оплаты → cashchannel.name
- Контрагент → financialoperation.counterparty (VARCHAR, текст)
- Даты платежей → payment_order_planed_date, payment_order_actual_date, act_planed_date, act_actual_date

---

## Финансовые операции

### deal_profit_calculator_financialoperation
Колонки: id, operation_type VARCHAR ('EXPENDITURE'|'PROFITABLE'), payment_description,
reason, unit_price numeric(105,5), tax, tax_amount, price_with_tax,
payment_order_planed_date, payment_order_actual_date, act_planed_date, act_actual_date,
comment, created_at, updated_at, base_financial_operation_id, currency_unit_id,
measurement_unit_id, parent_id, status_id, is_active, cash_channel_id,
contract_id, amount numeric(105,5), is_duty, price_by_documents, tax_in_sum,
no_tax, counterparty VARCHAR(250), planned_price, formula, company_id, custom_sum
ВАЖНО: counterparty — VARCHAR(250), НЕ FK
FKs: base_financial_operation_id → deal_profit_calculator_basefinancialoperation.id,
     currency_unit_id → deal_profit_calculator_currencyunit.id,
     cash_channel_id → deal_profit_calculator_cashchannel.id,
     contract_id → filemanager_contract.id,
     company_id → deal_company.draggable_ptr_id,
     measurement_unit_id → deal_profit_calculator_measurementunit.id

### deal_profit_calculator_computedfinancialoperation
Колонки: id, name VARCHAR (название комиссии), formula TEXT (формула расчёта),
value numeric(105,5) (рассчитанное значение), created_at, updated_at, actual_date,
cash_channel_id, comment, counterparty VARCHAR(250), planned_value,
base_financial_operation_id, document_bdr, document_bds, is_percent
FKs: base_financial_operation_id → deal_profit_calculator_basefinancialoperation.id,
     cash_channel_id → deal_profit_calculator_cashchannel.id
СВЯЗЬ С DEAL: через отдельную M2M таблицу (аналогично financialoperation, используй
deal_deal_financial_operations если нужна привязка к сделке — иначе через deal_profit_calculator_id)

### deal_deal_financial_operations — M2M сделки ↔ финоперации
Колонки: id, deal_id → deal_deal.draggable_ptr_id, financialoperation_id → deal_profit_calculator_financialoperation.id
ПРАВИЛО: это ЕДИНСТВЕННЫЙ способ связать financialoperation с deal_deal!

### deal_profit_calculator_basefinancialoperation — справочник типов операций
Колонки: id, name VARCHAR (человеческое название), type VARCHAR, created_at, updated_at,
author_id, group_id, parent_id
type: 'FOR_STANDARD' (стандартная) | 'FOR_COMPUTED' (вычисляемая комиссия)
Реальные значения name: 'Покупка товара/услуги', 'Продажа товара/услуги',
'Таможенная пошлина', 'Транспорт до границы', 'Транспорт после границы',
'БР', 'Процент по кредиту', 'НДС+Таможенный сбор', 'Репатриация',
'Ненормативные расходы', 'Комиссия 1'…'Комиссия 10', 'Прочие после налогов'

### deal_profit_calculator_cashchannel — каналы оплаты (банки)
Колонки: id, name VARCHAR — ТОЛЬКО name, нет abbreviation!
Реальные значения: 'Breekana Limited', 'CITIBANK EUROPE PLC', 'Emirates Islamic Bank', 'ICBC', ...

### deal_profit_calculator_currencyunit — валюты
Колонки: id, name VARCHAR (например 'RUB', 'USD', 'EUR')

### deal_profit_calculator_currencyunitconverter — курсы валют
Колонки: id, coefficient, currency_unit_1_id, currency_unit_2_id
ВНИМАНИЕ: несколько записей для одной пары → используй LATERAL JOIN LIMIT 1

---

## Товары и продуктовые единицы

### deal_profit_calculator_product — справочник товаров
Колонки: id, name VARCHAR(500), inventory_number, full_name, created_at, updated_at,
group_id → deal_profit_calculator_productgroup.id, manufacturer_id, place_of_manufacture_id

### deal_profit_calculator_productunit — позиции товара в сделке (SOLD/PURCHASED)
Колонки: id, type VARCHAR ('PURCHASED'=закупка | 'SOLD'=продажа),
unit_price numeric(105,5), planned_amount, actual_amount, resident,
contract_date, contract_condition, created_at, updated_at, author_id,
currency_unit_id, measurement_unit_id, product_id, provider_unit_id,
actual_date, planed_date, contract_id, company_unit_id, manufacturer_company_id, deal_id
FKs: deal_id → deal_deal.draggable_ptr_id,
     product_id → deal_profit_calculator_product.id,
     company_unit_id → deal_company.draggable_ptr_id  (грузополучатель/покупатель),
     manufacturer_company_id → deal_company.draggable_ptr_id  (производитель),
     currency_unit_id → deal_profit_calculator_currencyunit.id,
     measurement_unit_id → deal_profit_calculator_measurementunit.id,
     provider_unit_id → deal_profit_calculator_providerunit.id

### deal_profit_calculator_financialoperation_product_units — M2M финоперация ↔ продуктовая единица
Колонки: id, financialoperation_id → deal_profit_calculator_financialoperation.id,
         productunit_id → deal_profit_calculator_productunit.id
СТРОК: ~938. Это ПРАВИЛЬНЫЙ способ получить товар/грузополучателя/производителя
       по финоперации (в формате К5 каждая строка = одна finop)!
ПРАВИЛО: используй эту таблицу когда нужно от finop попасть к productunit.

### deal_profit_calculator_measurementunit — единицы измерения
Колонки: id, name VARCHAR

### deal_profit_calculator_providerunit — поставщики в сделке
Колонки: id, name, contract, contract_condition, contract_date, serial_number,
address, description, created_at, updated_at, author_id, cash_channel_id,
shipper_company_id, deal_id
FKs: shipper_company_id → deal_company.draggable_ptr_id  (грузоотправитель/поставщик),
     deal_id → deal_deal.draggable_ptr_id

### deal_profit_calculator_productgroup — группы (бюджеты) продуктов
Колонки: id, name

### deal_profit_calculator_projectexportsettings — настройки экспорта (одна строка)
Колонки: id, default_company VARCHAR (='КЕМ'), use_prefix BOOLEAN, default_bank_for_comissions_id
Использование: CROSS JOIN deal_profit_calculator_projectexportsettings pes
               → pes.default_company = 'КЕМ' для заполнения "юр. лицо"

---

## Сделки

### deal_deal — сделки (PK = draggable_ptr_id, НЕ id!)
Колонки: draggable_ptr_id (PK), name, registration_number VARCHAR(15),
deal_number INTEGER (порядковый, НЕ 'К5-25'!), sum numeric(12,2), is_draft, has_order,
is_win, description, slug, created_at, updated_at, author_id, contact_id,
deal_profit_calculator_id, status_id, last_update_analytics

### deal_dealstatus — статусы сделок (PK = draggablestatus_ptr_id)
Имя → bp_draggablestatus.name WHERE id = deal_dealstatus.draggablestatus_ptr_id
Реальные статусы: 'Активная', 'Черновик', 'Завершена', 'В архиве'

### deal_company — компании/контрагенты (PK = draggable_ptr_id, НЕ id!)
Колонки: draggable_ptr_id (PK), name, contractor_name, contractor_inn,
legal_form, legal_entity, is_resident, country, party, description,
reliability_comment, reliability_type, created_at, updated_at, status_id, author_id

### deal_interactionform — привязка компаний к сделке (M2M, может быть несколько компаний на сделку)
Колонки: id, name, company_id → deal_company.draggable_ptr_id,
deal_id → deal_deal.draggable_ptr_id, created_at, updated_at

### deal_task — задачи по сделке
Колонки: id, name, description, deadline, priority INTEGER, is_draft, created_at, updated_at,
stage_id, task_type_id, status_id, parent_id, author_id, deal_id → deal_deal.draggable_ptr_id

---

## Сотрудники

### auth_user — пользователи Django
Колонки: id, username, first_name, last_name, email, is_active, date_joined
Реальные пользователи: LotovD (Дмитрий Лотов), vskotov (Вениамин Котов), HelenNik (Елена Никашина)

### staff_businessrole — бизнес-роли (содержат реальных сотрудников)
Колонки: id, name VARCHAR(250), is_staff BOOLEAN, description, created_at, updated_at
is_staff=TRUE → реальный сотрудник. Реальные имена: 'Дмитрий Лотов', 'Вениамин Котов', 'Елена Никашина', 'Владимир Рыбкин'

### staff_profile — профили сотрудников
Колонки: id, user_id → auth_user.id, position VARCHAR(500), department VARCHAR(500),
phone, start_time_of_work, is_administrator, is_online, created_at, updated_at
M2M: business_roles → staff_businessrole через staff_profile_business_roles

### staff_department — отделы
Колонки: id, name, created_at, updated_at

### deal_deal_leaders — ответственные за сделку (M2M)
Колонки: id, deal_id → deal_deal.draggable_ptr_id, businessrole_id → staff_businessrole.id

---

## Контракты и файлы

### filemanager_contract — договоры
Колонки: id, name, number, date, repatriation_date, comment, created_at, updated_at,
document_id, currency_unit_id, company_id → deal_company.draggable_ptr_id

### bp_draggablestatus — таблица имён статусов
Колонки: id, name, color, created_at, updated_at
Используй для получения имени статуса сделки:
JOIN bp_draggablestatus bds ON bds.id = d.status_id

---

## SQL-паттерны для Excel-обогащения

### Паттерн 1: Товар, количество, ЕдИзм, грузополучатель, производитель (из шапки сделки)
```sql
SELECT
    d.registration_number,
    p.name              AS "Товар",
    pu.planned_amount   AS "Количество",
    mu.name             AS "ЕдИзм",
    c_buyer.name        AS "Грузополучатель",
    c_mfr.name          AS "Производитель"
FROM deal_deal d
LEFT JOIN LATERAL (
    SELECT * FROM deal_profit_calculator_productunit
    WHERE deal_id = d.draggable_ptr_id AND type = 'SOLD'
    ORDER BY id LIMIT 1
) pu ON true
LEFT JOIN deal_profit_calculator_product p ON p.id = pu.product_id
LEFT JOIN deal_profit_calculator_measurementunit mu ON mu.id = pu.measurement_unit_id
LEFT JOIN deal_company c_buyer ON c_buyer.draggable_ptr_id = pu.company_unit_id
LEFT JOIN deal_company c_mfr   ON c_mfr.draggable_ptr_id   = pu.manufacturer_company_id
WHERE d.registration_number IN ('К5-25', 'К6-25')
```

### Паттерн 2: Поставщик/грузоотправитель (из providerunit)
```sql
LEFT JOIN LATERAL (
    SELECT c.name AS supplier_name
    FROM deal_profit_calculator_providerunit pv
    LEFT JOIN deal_company c ON c.draggable_ptr_id = pv.shipper_company_id
    WHERE pv.deal_id = d.draggable_ptr_id
    ORDER BY pv.id LIMIT 1
) pv ON true
```

### Паттерн 3: Все финоперации сделки с именем типа
```sql
FROM deal_deal d
JOIN deal_deal_financial_operations dfo ON dfo.deal_id = d.draggable_ptr_id
JOIN deal_profit_calculator_financialoperation fo ON fo.id = dfo.financialoperation_id
JOIN deal_profit_calculator_basefinancialoperation bfo ON bfo.id = fo.base_financial_operation_id
LEFT JOIN deal_profit_calculator_currencyunit cu ON cu.id = fo.currency_unit_id
LEFT JOIN deal_profit_calculator_cashchannel ch ON ch.id = fo.cash_channel_id
-- bfo.name = 'Покупка товара/услуги', fo.operation_type = 'EXPENDITURE'|'PROFITABLE'
```

### Паттерн 4: Статус сделки
```sql
JOIN bp_draggablestatus bds ON bds.id = d.status_id
-- bds.name = 'Активная', 'Завершена', 'В архиве', 'Черновик'
```

### Паттерн 5: Конвертация валюты (без дублирования строк)
```sql
LEFT JOIN LATERAL (
    SELECT coefficient FROM deal_profit_calculator_currencyunitconverter
    WHERE currency_unit_1_id = fo.currency_unit_id
      AND currency_unit_2_id = (SELECT id FROM deal_profit_calculator_currencyunit WHERE name = 'USD')
    ORDER BY id DESC LIMIT 1
) conv ON true
```

### Паттерн 6: Ответственный менеджер сделки
```sql
LEFT JOIN LATERAL (
    SELECT br.name AS manager_name
    FROM deal_deal_leaders dl
    JOIN staff_businessrole br ON br.id = dl.businessrole_id
    WHERE dl.deal_id = d.draggable_ptr_id AND br.is_staff = true
    ORDER BY dl.id LIMIT 1
) mgr ON true
```

### Паттерн 7: Компании сделки (через interactionform)
```sql
-- Может быть несколько компаний на сделку!
JOIN deal_interactionform inf ON inf.deal_id = d.draggable_ptr_id
JOIN deal_company c ON c.draggable_ptr_id = inf.company_id
```

### Паттерн 8: К5-стиль — товар/грузополучатель/производитель через finop (НЕ через deal!)
```sql
-- В К5 каждая строка = одна финоперация. Товар берётся через M2M financialoperation_product_units!
-- НЕ используй productunit.deal_id напрямую — это дублирует строки!
FROM deal_profit_calculator_financialoperation fo
JOIN deal_profit_calculator_basefinancialoperation bfo ON bfo.id = fo.base_financial_operation_id
JOIN deal_deal_financial_operations ddfo ON ddfo.financialoperation_id = fo.id
JOIN deal_deal d ON d.draggable_ptr_id = ddfo.deal_id
JOIN deal_profit_calculator_currencyunit cu ON cu.id = fo.currency_unit_id
LEFT JOIN deal_profit_calculator_cashchannel cc ON cc.id = fo.cash_channel_id
CROSS JOIN deal_profit_calculator_projectexportsettings pes
-- Товар, грузополучатель, производитель из M2M:
LEFT JOIN LATERAL (
    SELECT p.name AS pname, pg.name AS gname,
           pu.company_unit_id, pu.manufacturer_company_id,
           pu.contract_id, pu.planed_date, pu.contract_condition, pu.type AS pu_type
    FROM deal_profit_calculator_financialoperation_product_units fopu
    JOIN deal_profit_calculator_productunit pu ON pu.id = fopu.productunit_id
    JOIN deal_profit_calculator_product p ON p.id = pu.product_id
    LEFT JOIN deal_profit_calculator_productgroup pg ON pg.id = p.group_id
    WHERE fopu.financialoperation_id = fo.id
    ORDER BY pu.id LIMIT 1
) pu_info ON true
LEFT JOIN deal_company c_buyer ON c_buyer.draggable_ptr_id = pu_info.company_unit_id
LEFT JOIN deal_company c_mfr ON c_mfr.draggable_ptr_id = pu_info.manufacturer_company_id
-- Поставщик:
LEFT JOIN LATERAL (
    SELECT dc.name AS supplier_name
    FROM deal_profit_calculator_providerunit pv
    JOIN deal_company dc ON dc.draggable_ptr_id = pv.shipper_company_id
    WHERE pv.deal_id = d.draggable_ptr_id
    ORDER BY pv.id LIMIT 1
) pv ON true
WHERE bfo.name IN ('Покупка товара/услуги', 'Продажа товара/услуги')
  AND d.registration_number LIKE 'К%'
-- pu_info.pname → Товар
-- c_buyer.name → Грузополучатель
-- c_mfr.name → Производитель
-- pv.supplier_name → Поставщик/Грузоотправитель
-- pes.default_company → Юр. лицо (= 'КЕМ')
```
"""


def get_wiki_context() -> str:
    """
    Возвращает строку-контекст для системного промпта агента
    в зависимости от текущего режима (chemico_kb_mode).

    Режим 0 → пустая строка (агент без подсказок)
    Режим 1 → документация по схеме Chemico
    Режим 2 → схема Chemico + дополнительный Wiki-раздел
    """
    from recordings.models import SystemConfig

    mode = SystemConfig.get('chemico_kb_mode', '1').strip()

    if mode == '0':
        logger.info('chemico_agent KB: режим 0 — без базы знаний')
        return ''

    # Режим 1 и 2: загружаем основную Wiki-статью
    schema_ctx = _load_wiki_article(WIKI_SLUG, fallback=BUILTIN_SCHEMA_DOCS)
    result = f'\n\n## База знаний: схема БД Chemico\n\n{schema_ctx}'

    if mode == '2':
        extra_slug = SystemConfig.get('chemico_kb_extra_slug', '').strip()
        if extra_slug:
            extra_ctx = _load_wiki_article(extra_slug, fallback=None)
            if extra_ctx:
                result += f'\n\n## Дополнительный контекст (Wiki: {extra_slug})\n\n{extra_ctx}'
                logger.info('chemico_agent KB: режим 2 — схема + Wiki[%s]', extra_slug)
            else:
                logger.warning('chemico_agent KB: режим 2, доп. статья не найдена: %s', extra_slug)
        else:
            logger.warning('chemico_agent KB: режим 2, но chemico_kb_extra_slug не задан')

    logger.info('chemico_agent KB: режим %s, контекст %d символов', mode, len(result))
    return result


def _load_wiki_article(slug: str, fallback: str | None) -> str | None:
    """Загрузить Wiki-статью по slug. Если не найдена — вернуть fallback."""
    try:
        from wiki_kb.models import WikiArticle
        article = WikiArticle.objects.filter(slug=slug).first()
        if article and article.content:
            return article.content
    except Exception as e:
        logger.warning('chemico_agent: не удалось загрузить Wiki[%s]: %s', slug, e)
    return fallback


def ensure_wiki_article() -> str:
    """
    Создать/обновить Wiki-статью chemico-db-schema актуальной схемой.
    Вызывается из management command create_chemico_wiki И автоматически
    при первой сборке агента (если статья устарела).
    Возвращает URL статьи.
    """
    from wiki_kb.models import WikiArticle
    from recordings.models import SiteUser

    site_admin = SiteUser.objects.first()
    if not site_admin:
        raise RuntimeError('Нет SiteUser для создания Wiki-статьи')

    article, created = WikiArticle.objects.update_or_create(
        slug=WIKI_SLUG,
        defaults={
            'title': 'Chemico — Схема базы данных (KB)',
            'content': BUILTIN_SCHEMA_DOCS,
            'created_by': site_admin,
            'updated_by': site_admin,
            'is_personal': False,
        },
    )
    action = 'создана' if created else 'обновлена'
    logger.info('Wiki-статья %s: %s', WIKI_SLUG, action)
    return f'/kb/{WIKI_SLUG}/'


def auto_ensure_wiki_article() -> None:
    """
    Обновляет chemico-db-schema если её содержимое устарело.
    Вызывается из build_agent() при первой сборке — не требует ручного запуска команды.
    """
    try:
        from wiki_kb.models import WikiArticle
        article = WikiArticle.objects.filter(slug=WIKI_SLUG).first()
        # Обновляем если статьи нет, или если в ней нет маркера актуальной версии
        if not article or 'draggable_ptr_id' not in (article.content or ''):
            ensure_wiki_article()
            logger.info('chemico_agent KB: wiki-статья схемы обновлена автоматически')
    except Exception as e:
        logger.warning('chemico_agent KB: не удалось автообновить wiki-статью: %s', e)
