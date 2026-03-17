#!/usr/bin/env python
"""Update wiki article 130 with improved K5 export instructions."""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetrec.settings')
sys.path.insert(0, '/app')
import django
django.setup()

from wiki_kb.models import WikiArticle

a = WikiArticle.objects.get(id=130)

a.content = r"""# Шаблон выгрузки К5: полная инструкция для агента

**Источник:** `К5_2521.xlsx`
**Версия инструкции:** 2.1 (на основе export_utils.py из bp-platform + сравнение с реальным файлом)
**База данных:** chemico_db (PostgreSQL, доступна через CHEMICO_DB_URL)

---

## Общий принцип

Выгрузка формирует Excel с **одним или двумя листами**:

| Лист | Содержание | Когда |
|------|-----------|-------|
| **План** | Плановые строки (БДС + БДР) | Всегда |
| **Реализация** | Фактические строки (БДС + БДР + документы) | Только если сделка прошла этап «Реализация сделки» |

> Если сделка не прошла реализацию — только лист «План». Оба листа имеют одинаковый набор из **55 колонок** (плюс авто-индекс pandas «№ п/п» = колонка A).

---

## Структура колонок (55 штук, B–BD)

| Кол. | Имя | Источник / Логика заполнения |
|------|-----|------------------------------|
| B | **№ сделки** | `deal.registration_number` если сделка прошла реализацию; иначе `None` |
| C | **дата регистрации сделки** | Дата последнего этапа «Реализация сделки» (`Stage WHERE name='Реализация сделки'`), формат `ДД.ММ.ГГГГ`; `None` если этап не пройден |
| D | **юр. лицо** | `ProjectExportSettings.default_company` — **аббревиатура** юрлица из настроек экспорта (например «КЕМ», не полное название) |
| E | **юр.лицо (банк)** | `D + " " + fo.cash_channel.abbreviation` если у канала есть abbreviation; иначе `D + " " + cash_channel.name`; пример: «КЕМ РФБ», «КЕМ ВТБ» |
| F | **тип операции** | БДС-строка: `списание` (EXPENDITURE) / `зачисление` (INCOME); БДР-строка: `расходы` / `доходы` |
| G | **расшифровка операции** | `base_financial_operation.name` с заменами: «Покупка товара/услуги» → «Товар покупка», «Продажа товара/услуги» → «Товар продажа», «Процент по кредиту» → «% по кредиту» |
| H | **дата операции** | БДС-план: `fo.payment_order_planed_date`; БДС-факт: `fo.payment_order_planed_date`; БДР: `fo.act_planed_date` или `fo.act_actual_date`; БДС-документ: `bds.document_date_bds`; БДР-документ: `bdr.document_date_bdr`; формат `ДД.ММ.ГГГГ` |
| I | **месяц операции** | Формула Excel: `=MONTH(H{row})` |
| J | **год операции** | Формула Excel: `=YEAR(H{row})` |
| K | **бюджет** | `БДС` (обычные операции), `БДСк` (операции начинающиеся с «Комиссия »), `БДР` (БДР-строки) |
| L | **сумма (в т.ч. НДС), руб.** | Формула Excel: `=IF(P{row}="RUB",O{row},ROUND(O{row}*S{row},2))` — КРОМЕ строк «% по кредиту» |
| M | **сумма НДС, руб.** | Формула Excel: `=$L{row} / {(1+tax/100)} * {tax/100}` если есть НДС (`fo.no_tax=False` и `fo.tax>0`); `0` если НДС нет |
| N | **сумма без НДС, руб.** | Формула Excel: `=$L{row} - $M{row}` |
| O | **сумма в валюте** | Плановая: `fo.price_with_tax` (план); Фактическая: `fo.price_with_tax_difference`; BDS-документ: `bds.amount`; BDR-документ: `bdr.amount`; округление 2 знака |
| P | **валюта** | `fo.currency_unit.name` (например `RUB`, `USD`, `EUR`) |
| Q | **комментарии к платежу** | `fo.comment`; может содержать «НДС - {сумма}» |
| R | **основание платежа** | `fo.reason`; у БДР-строк и строк «% по кредиту» = `None` |
| S | **курс** | Коэффициент конвертации валюты → RUB из `deal_profit_calculator_currencyunitconverter`; план: плановый курс из `analytical_profit_structure`; факт: актуальный курс; `1` для RUB |
| T | **дата курса** | Дата регистрации сделки если валюта ≠ RUB; BDS/BDR: `consolidation_conversion_rate_date`; `None` для RUB |
| U | **кол-во** | `fo.amount`; для «% по кредиту»: суммарное количество по операциям «Продажа товара/услуги» этой сделки |
| V | **единица измерения** | `sold_pu.measurement_unit.name`; для «% по кредиту»: «шт» |
| W | **документ** | `bds.document_bds` / `bdr.document_bdr`; иначе `None` |
| X | **№ документа** | `bds.document_number_bds` / `bdr.document_number_bdr`; иначе `None` |
| Y | **дата документа** | Формула Excel: `=H{row}` |
| Z | **печать** | `bdr.seal`; для «% по кредиту»: строка «{%}, {кол-во дней} дн.»; иначе `None` |
| AA | **комментарий к документу / назначение платежа** | Формула Excel: `=G{row}&" "&R{row}&" "&O{row}&" "&P{row}` если есть НДС; иначе `=G{row}&" "&O{row}&" "&P{row}` |
| AB | **сч-ф** | `bdr.invoice`; иначе `None` |
| AC | **№ договора** | `fo.contract.number` → `purchase_pu.contract.number` → `sold_pu.contract.number` |
| AD | **дата договора** | Дата договора в формате `ДД.ММ.ГГГГ` |
| AE | **УНК** | `bds.ucn` / `bdr.ucn`; иначе `None` |
| AF | **контрагент** | EXPENDITURE: `purchase_pu.company_unit.name`; INCOME: `sold_pu.company_unit.name`; «% по кредиту»: «Кредитор»; «Прочие после налогов»: «Сальников» |
| AG | **товар** | Имена продуктов через «, » из `fo.product_units → product.name` |
| AH | **поставщик** | `purchase_pu.company_unit.name` (тип PURCHASED) |
| AI | **договор с поставщиком** | `purchase_pu.contract.number` |
| AJ | **Основание к договору с поставщиком** | `purchase_pu.contract.name + " от " + purchase_pu.contract.date (ДД.ММ.ГГГГ)` |
| AK | **покупатель** | `sold_pu.company_unit.name` (тип SOLD) |
| AL | **договор с покупателем** | `sold_pu.contract.number` |
| AM | **Основание к договору с покупателем** | `sold_pu.contract.name + " от " + sold_pu.contract.date (ДД.ММ.ГГГГ)` |
| AN | **производитель** | `purchase_pu.manufacturer_company.name` приоритетно; иначе `product.manufacturer.name` |
| AO | **грузоотправитель** | `purchase_pu.provider_unit.shipper_company.name` |
| AP | **грузополучатель** | `sold_pu.provider_unit.shipper_company.name + " (" + address + ")"` — если address = None, показывает «(None)» — **это нормальное поведение из оригинала** |
| AQ | **Платёжный агент** | Компании с interaction_form=«Платёжный агент» через «/»; если нет — `"-"` (дефис, не пусто) |
| AR | **план/факт** | `"факт"` если `fo.payment_order_actual_date IS NOT NULL`; иначе `"план"` |
| AS | **план//факт** | Формула Excel: `=AR{row}` |
| AT | **период закрытия** | `expense_export_data.closure_period` (только для БР бюджета); иначе `None` |
| AU | **статус** | `fo.status.name` если есть; иначе `None` |
| AV | **НОП/МП** | Бизнес-роль «РОП» + «/» + бизнес-роль «МП» из `deal.leaders ∪ deal.contractors`; если оба пусты — `None` |
| AW | **СРК** | Сотрудники с бизнес-ролью «СРК» через «,» |
| AX | **примечание** | `cfo.note` / `bds.note` / `bdr.note`; иначе `None` |
| AY | **поставщик (курс на день)** | `purchase_pu.contract_condition`; пример: «На день первичных документов», «Предоплата» |
| AZ | **покупатель (курс на день)** | `sold_pu.contract_condition`; пример: «На день первичных документов», «Предоплата» |
| BA | **Комиссионер** | `cfo.commission_agent`; иначе `None` |
| BB | **кл-во тр ед** | Пока не заполняется (`None`) |
| BC | **дата поставки** | `fo.planed_date` в формате `ДД.ММ.ГГГГ` |
| BD | **Статус** | `"факт"` если есть фактическая дата; `"план"` иначе |

---

## Сколько строк порождает одна финансовая операция (FO)

### Если сделка НЕ прошла реализацию → лист «План»

```
1 строка БДС: бюджет=БДС, тип=расходы/доходы, дата=payment_order_planed_date, план/факт=план
1 строка БДР: бюджет=БДР, тип=расходы/доходы, дата=act_planed_date, план/факт=план
```

### Если сделка прошла реализацию → лист «План» + лист «Реализация»

**Лист «План»:**
```
1 строка БДС: сумма=planned_price, дата=payment_order_planed_date, план/факт=план
1 строка БДР: сумма=planned_price, дата=act_planed_date, план/факт=план
```

**Лист «Реализация»:**
```
1 строка БДС: сумма=price_with_tax_difference, дата=payment_order_planed_date, план/факт=факт/план
+ N строк БДС-документов: по одной на каждый bds_object (bds.document_date_bds, bds.amount), план/факт=факт
1 строка БДР: сумма=price_by_documents_difference, дата=act_actual_date/act_planed_date, план/факт=факт/план
+ N строк БДР-документов: по одной на каждый bdr_object (bdr.document_date_bdr, bdr.amount), план/факт=факт
```

### Специальные случаи

**Комиссии** (`base_financial_operation.name` начинается с «Комиссия »):
- Все строки: бюджет = **БДСк** вместо БДС
- Контрагент AF: «Комиссия 2/3» → `shipper_company`; «Комиссия 4-8» → `company_unit`; «Прочие после налогов» → «Сальников»

**Процент по кредиту** (`base_financial_operation.name = 'Процент по кредиту'`):
- бюджет = **БДР**, тип = расходы/доходы
- U (кол-во) = сумма `amount` по всем операциям «Продажа товара/услуги» этой сделки
- V (единица) = «шт»
- AF (контрагент) = «Кредитор»
- Если базовая валюта = RUB: P, S, T, O = `None`

---

## Порядок строк в каждом листе

```
1. Стандартные БДС: Покупка → Транспорт до границы → Комиссии банк → Таможня
   → НДС+Сбор → Брокер → Транспорт после границы → Прочие до налогов
   → Продажа → Остальные
2. Комиссии БДС (БДСк)
3. Стандартные БДР (те же группы, без % по кредиту)
4. Комиссии БДР
5. % по кредиту (БДР) — всегда последними
```

---

## SQL-подсказки

```sql
-- Основные таблицы chemico_db
deal_deal d                                            -- сделки (ключ: draggable_ptr_id)
deal_profit_calculator_financialoperation fo           -- финансовые операции
deal_profit_calculator_basefinancialoperation bfo      -- типы операций (name: 'Покупка товара/услуги' и т.д.)
deal_profit_calculator_productunit pu                  -- товарные единицы (type: PURCHASED/SOLD)
deal_profit_calculator_product p                       -- товары
deal_profit_calculator_currencyunit cu                 -- валюты
deal_profit_calculator_currencyunitconverter cv        -- курсы
deal_profit_calculator_dealprofitcalculate dpc         -- калькулятор рентабельности
deal_profit_calculator_projectexportsettings pes       -- настройки (юрлицо = abbreviation)
deal_profit_calculator_cashflowbudget bds              -- БДС документы (bds_objects)
deal_profit_calculator_incomeexpensesbudget bdr        -- БДР документы (bdr_objects)
deal_profit_calculator_expenceexportdata eed           -- доп поля для БР бюджета

-- Связи FO → ProductUnit
deal_profit_calculator_financialoperation_product_units (financialoperation_id, productunit_id)

-- Связи FO → Deal
deal_profit_calculator_financialoperation_deals (financialoperation_id, deal_id)

-- Фильтр К5: сделки с регномером начинающимся на «К»
WHERE d.registration_number ~ '^К'

-- Тип операции
fo.operation_type IN ('EXPENDITURE', 'INCOME')

-- Тип ProductUnit
pu.type IN ('PURCHASED', 'SOLD')

-- Курс: coefficient из currencyunitconverter WHERE currency_unit_1.name='RUB'
-- Для RUB → RUB: coefficient = 1.0
```

---

## Пример базового SQL-запроса (для одного листа без BDS/BDR)

```sql
SELECT
    d.registration_number                   AS "№ сделки",
    TO_CHAR(d.created_at AT TIME ZONE 'UTC', 'DD.MM.YYYY') AS "дата регистрации сделки",
    pes.default_company                     AS "юр. лицо",
    pes.default_company || ' ' || COALESCE(cc.abbreviation, cc.name) AS "юр.лицо (банк)",
    CASE fo.operation_type
        WHEN 'EXPENDITURE' THEN 'списание'
        ELSE 'зачисление' END               AS "тип операции",
    -- G: расшифровка (с заменами имён)
    CASE
        WHEN bfo.name = 'Покупка товара/услуги'  THEN 'Товар покупка'
        WHEN bfo.name = 'Продажа товара/услуги'  THEN 'Товар продажа'
        WHEN bfo.name = 'Процент по кредиту'     THEN '% по кредиту'
        ELSE bfo.name END                   AS "расшифровка операции",
    COALESCE(fo.payment_order_planed_date::date,
             fo.act_planed_date::date)::text AS "дата операции",
    fo.price_with_tax                       AS "сумма в валюте",
    cu.name                                 AS "валюта",
    fo.comment                              AS "комментарии к платежу",
    fo.reason                               AS "основание платежа",
    COALESCE(cv.coefficient, 1.0)           AS "курс",
    fo.amount                               AS "кол-во"
FROM deal_profit_calculator_financialoperation fo
JOIN deal_profit_calculator_basefinancialoperation bfo ON bfo.id = fo.base_financial_operation_id
JOIN deal_profit_calculator_financialoperation_deals fod ON fod.financialoperation_id = fo.id
JOIN deal_deal d ON d.draggable_ptr_id = fod.deal_id
JOIN deal_profit_calculator_currencyunit cu ON cu.id = fo.currency_unit_id
LEFT JOIN deal_profit_calculator_cashchannel cc ON cc.id = fo.cash_channel_id
LEFT JOIN deal_profit_calculator_currencyunitconverter cv
    ON cv.currency_unit_1_id = (SELECT id FROM deal_profit_calculator_currencyunit WHERE name = 'RUB')
   AND cv.currency_unit_2_id = fo.currency_unit_id
   AND cv.coefficient > 0
CROSS JOIN deal_profit_calculator_projectexportsettings pes
WHERE d.registration_number ~ '^К'
ORDER BY d.registration_number, fo.id;
```

---

## Как использовать

Пользователь пишет агенту:

```
/db заполни шаблон К5_2521.xlsx — сделай два листа: «План» и «Реализация»
```

Агент:
1. Читает эту статью как инструкцию
2. Выполняет SQL к chemico_db (через CHEMICO_DB_URL)
3. Формирует Excel: лист «План» + лист «Реализация» (если есть фактические сделки)
4. Вставляет Excel-формулы: MONTH/YEAR/IF для L/M/N/I/J/Y/AA/AS
5. Возвращает `.xlsx` файл
"""

a.save()
print(f"Saved article 130, length = {len(a.content)}")
