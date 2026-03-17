#!/usr/bin/env python
"""
Создать большую wiki-статью: База знаний Chemico BP Platform.
Запуск внутри контейнера: python /app/create_chemico_wiki_full.py
"""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetrec.settings')
django.setup()

from wiki_kb.models import WikiArticle, index_wiki_article

SLUG = 'chemico-bp-platform-knowledge-base'
TITLE = 'Chemico: база знаний по платформе BusinessPad (схема БД, бизнес-логика, SQL)'

CONTENT = r'''
# Chemico: База знаний платформы BusinessPad

> Эта статья описывает структуру базы данных, бизнес-логику и SQL-паттерны для работы с системой Chemico на платформе BusinessPad. Используется AI-агентом для корректных ответов на вопросы.

---

## 1. Общая архитектура системы

**BusinessPad** — CRM/BPM-платформа на Django, развёрнутая для компании **Chemico** (торговля аналитическим и лабораторным оборудованием, химреактивами).

Ключевые модули и их таблицы:

| Модуль | Префикс таблиц | Назначение |
|--------|---------------|-----------|
| Бизнес-процессы | `bp_*` | Шаблоны процессов, этапы, условия, сроки |
| Сделки | `deal_*` | Коммерческие сделки, воронка продаж, задачи |
| CRM | `crm_*` / `deal_company`, `staff_contact` | Клиенты, контактные лица, контрагенты |
| Сотрудники | `staff_*` | Пользователи, роли, отделы, права |
| Файлы | `filemanager_*` | Вложения к сделкам и профилям |
| Авторизация | `auth_*` | Стандартные Django-пользователи |
| Финансы | `deal_profit_calculator_*` | Финансовые операции по сделкам |
| Комментарии | `comment_*` | Комментарии к любым объектам |
| Уведомления | `notification_*` | Система уведомлений |

---

## 2. Модуль Сотрудников (staff_*)

### 2.1 auth_user — базовые пользователи Django

Основная таблица пользователей системы.

```
Поля:
  id            INTEGER PRIMARY KEY
  username      VARCHAR(150) UNIQUE   — логин (например: LotovD, vskotov, HelenNik)
  password      VARCHAR(128)          — хэш пароля (pbkdf2_sha256)
  first_name    VARCHAR(150)
  last_name     VARCHAR(150)
  email         VARCHAR(254)
  is_staff      BOOLEAN               — доступ в Django Admin
  is_active     BOOLEAN               — активен ли аккаунт
  is_superuser  BOOLEAN               — суперпользователь
  last_login    TIMESTAMP WITH TIME ZONE
  date_joined   TIMESTAMP WITH TIME ZONE
```

**Реальные пользователи Chemico:**
- `adminbp` — Kirill Goncharov, support@bpcrm.ru (суперпользователь, техподдержка BP)
- `LotovD` — Дмитрий Лотов, dmitriy.lotov@chemi-co.com
- `vskotov` — Вениамин Котов, vskotov@chemi-co.com
- `HelenNik` — Елена Никашина, 2customer@chemi-co.com
- `SvUsik` — (пользователь системы)

### 2.2 staff_profile — расширенный профиль сотрудника

Связан с `auth_user` через OneToOne. Содержит рабочую информацию.

```
Поля:
  id                        INTEGER PRIMARY KEY
  user_id                   FK → auth_user (OneToOne)
  image_id                  FK → filemanager_file (аватар)
  position                  VARCHAR(500)   — должность
  department                VARCHAR(500)   — отдел (устаревшее, используй staff_department)
  phone                     VARCHAR(250)
  start_time_of_work        TIMESTAMP      — дата начала работы
  is_administrator          BOOLEAN        — администратор платформы
  send_email_notification   BOOLEAN        — получать email-уведомления
  is_online                 BOOLEAN
  last_online               TIMESTAMP WITH TIME ZONE
  created_at                TIMESTAMP WITH TIME ZONE
  updated_at                TIMESTAMP WITH TIME ZONE
```

**Связи:**
- M2M: `business_roles` → `staff_businessrole` (роли сотрудника в системе)
- M2M: `contacts` → `staff_contact` (контакты, связанные с сотрудником)

**Вычисляемые свойства:**
- `work_experience` — стаж в секундах от `start_time_of_work`
- `deals_ids` — ID сделок, где сотрудник является ответственным/исполнителем/автором

### 2.3 staff_businessrole — бизнес-роли

**КЛЮЧЕВАЯ КОНЦЕПЦИЯ.** Бизнес-роль — это абстракция над реальным сотрудником. Все назначения ответственности в системе (за сделки, этапы, задачи) идут через роли, а не через `auth_user` напрямую.

```
Поля:
  id            INTEGER PRIMARY KEY
  name          VARCHAR(250)   — имя роли или имя человека (если is_staff=true)
  description   TEXT
  role_type_id  FK → staff_businessroletype (NULL для персональных ролей)
  is_staff      BOOLEAN        — true = реальный сотрудник, false = абстрактная роль
  created_at    TIMESTAMP WITH TIME ZONE
  updated_at    TIMESTAMP WITH TIME ZONE
```

**Связи:**
- M2M: `parents` → self (иерархия ролей, роль может быть потомком другой)
- M2M: `business_permission_groups` → через `staff_businesspermissiongrouptobusinessrole`
- Через `staff_profile.business_roles` — связь роли с реальным человеком

**Реальные роли Chemico:**
- id=1: `Kirill Goncharov` (is_staff=true) — техподдержка
- id=2: `Гость` (is_staff=false) — роль только для просмотра
- id=3: `Невидимка` (is_staff=false) — роль без уведомлений
- id=4: `Дмитрий Лотов` (is_staff=true)
- id=18: `Владислав Васильев` (is_staff=true)

**Связь сотрудника с ролью:**
```sql
-- Найти роли конкретного сотрудника
SELECT br.id, br.name, br.is_staff
FROM staff_businessrole br
JOIN staff_profile_business_roles spbr ON br.id = spbr.businessrole_id
JOIN staff_profile sp ON spbr.profile_id = sp.id
JOIN auth_user au ON sp.user_id = au.id
WHERE au.username = 'LotovD';
```

### 2.4 staff_businessroletype — типы бизнес-ролей

Классификация ролей (например: менеджер, руководитель, технический специалист).

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)
  created_at, updated_at
```

### 2.5 staff_department — отделы

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)
  created_at, updated_at

Связи:
  M2M: higher_departments → self (иерархия отделов)
  M2M: leaders → staff_businessrole (руководители отдела)
  M2M: employees → staff_businessrole (сотрудники отдела)
```

### 2.6 staff_contact — контактные лица клиентов

```
Поля:
  id        INTEGER PRIMARY KEY
  name      VARCHAR(250)
  phone     VARCHAR(250)
  email     VARCHAR(250)
  position  VARCHAR(250)   — должность контакта в компании клиента
  created_at, updated_at
```

Используется в:
- `deal_deal.contact_id` — ответственное контактное лицо по сделке
- `staff_profile.contacts` (M2M) — контакты, которые ведёт сотрудник

---

## 3. Модуль Бизнес-Процессов (bp_*)

### 3.1 bp_businessprocess — шаблон/тип бизнес-процесса

**Шаблон** (не экземпляр!) процесса. Описывает как должен выглядеть тот или иной процесс в компании.

```
Поля:
  id                        INTEGER PRIMARY KEY
  name                      VARCHAR(250)     — название процесса
  type                      VARCHAR(100)     — тип: 'deal', 'company', 'financial_operation', 'expense_budget'
  is_template               BOOLEAN          — это шаблон для новых экземпляров
  is_draft                  BOOLEAN          — черновик (не используется)
  percent_from_deal         DECIMAL(12,2)    — % от суммы сделки для финансовых операций
  group_id                  FK → bp_group    — организационная группа
  owner_id                  FK → staff_businessrole  — владелец/руководитель процесса
  author_id                 FK → staff_businessrole  — автор создавший шаблон
  section_available         BOOLEAN          — включены ли секции/разделы
  permissions_available     BOOLEAN          — проверки прав на ответственных
  permission_entities       VARCHAR(50)[]    — список: {FO, Product, Contractors, Task}
  created_at, updated_at
```

**Типы бизнес-процессов:**
- `deal` — процесс для управления сделкой (основной тип)
- `company` — процесс регистрации нового контрагента
- `financial_operation` — процесс выполнения финансовой операции
- `expense_budget` — процесс бюджетирования расходов

**Реальные процессы Chemico:**
- id=1: `СДЕЛКА` (type=deal) — основной процесс продаж
- id=3: `Регистрация контрагента` (type=company)
- id=5: `Формирование БР` (type=expense_budget, group=5)
- id=10: `тест пуши` (type=deal, group=7)
- id=11: `Согласование договоров` (type=deal, group=5)

**Связи:**
- M2M: `leaders` — динамические ответственные за процесс
- M2M: `static_leaders` — статические ответственные (фиксированные роли)
- M2M: `contractors` — исполнители в процессе
- 1:M → `bp_businessprocesscomponent` — компоненты (этапы, шлюзы)
- 1:M → `bp_businessprocessduration` — сроки выполнения
- 1:M → `bp_businessprocessfield` — поля данных процесса

### 3.2 bp_group — группы бизнес-процессов

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)
  created_at, updated_at
```

### 3.3 bp_businessprocesscomponent — компоненты процесса

Полиморфная таблица для этапов и шлюзов (базовый класс).

```
Поля:
  id                    INTEGER PRIMARY KEY
  name                  TEXT                 — название компонента
  type                  VARCHAR(1)           — тип: этап, шлюз, и др.
  coords                VARCHAR(250)         — координаты на визуальной схеме
  business_process_id   FK → bp_businessprocess
  swimlane_id           FK → bp_swimlane     — дорожка ответственного
  polymorphic_ctype_id  FK → django_content_type  — реальный тип объекта
  section_id            FK → bp_section
  created_at, updated_at
```

### 3.4 bp_stage / bp_stagecomponent — этапы процесса

Этап — конкретный шаг в бизнес-процессе.

```
bp_stagecomponent (промежуточный полиморфный класс):
  businessprocesscomponent_ptr_id  FK → bp_businessprocesscomponent

bp_stage (конкретный этап):
  stagecomponent_ptr_id   FK → bp_stagecomponent
  parent_id               FK → bp_stagetemplate — шаблон этапа
  gateway_stage_id        FK → self              — предыдущий шлюз
  gateway_id              FK → bp_gateway        — следующий шлюз
  branch_id               FK → bp_branch

Типы этапов:
  '1' = START              — начальный этап
  '2' = COMMON             — обычный этап
  '3' = END                — завершающий этап
  '4' = START_IN_GATEWAY   — начало в условном переходе
  '5' = END_IN_GATEWAY     — конец в условном переходе
  '6' = CHECK_CONDITIONS   — проверка условий
```

**Ответственные за этап:**
- M2M: `leaders` — динамические ответственные за этап
- M2M: `static_leaders` — статические ответственные
- M2M: `contractors` — исполнители на этапе

### 3.5 bp_branch / bp_gateway — условные переходы

```
bp_gateway (шлюз — точка ветвления):
  stagecomponent_ptr_id   FK → bp_stagecomponent
  parent_id               FK → self (родительский шлюз)
  start_stage_id          FK → bp_stage (начальный этап шлюза)

bp_branch (ветвь от шлюза):
  id
  is_active       BOOLEAN
  is_finished     BOOLEAN
  gateway_id      FK → bp_gateway
  parent_id       FK → self (вложенные ветви)
  created_at, updated_at

bp_condition (условие для активации ветви):
  id
  first_operand   JSONB    — левая часть условия
  second_operand  JSONB    — правая часть условия
  operation       VARCHAR(3) — операция: ==, !=, <, >, <=, >=
  branch_template_id FK
  created_at, updated_at
```

### 3.6 bp_swimlane — дорожки (Swim Lanes)

Дорожки — визуальное разделение ответственности в схеме процесса.

```
Поля:
  id                    INTEGER PRIMARY KEY
  name                  VARCHAR(250)
  business_process_id   FK → bp_businessprocess
  created_at, updated_at

Связи:
  M2M: leaders → staff_businessrole
  M2M: static_leaders → staff_businessrole
```

### 3.7 bp_businessprocessduration — сроки выполнения

```
Поля:
  id                      INTEGER PRIMARY KEY
  duration                INTERVAL         — плановый срок (например: '7 days')
  recommended_duration    INTERVAL         — рекомендуемый срок
  max_duration            INTERVAL         — максимальный срок
  min_duration            INTERVAL         — минимальный срок
  end_time                TIMESTAMP WITH TIME ZONE  — конкретная дата окончания
  is_recommended          BOOLEAN
  can_be_transfer         BOOLEAN          — можно ли переносить срок
  can_be_blank            BOOLEAN          — срок может быть не задан
  business_process_id     FK → bp_businessprocess
  created_at, updated_at
```

**Уведомления по срокам:**
```
bp_businessprocessdurationnotification:
  id
  percent     INTEGER (0-100)    — % выполнения при котором срабатывает
  text        TEXT               — текст уведомления
  business_process_duration_id  FK
  template_id FK → bp_businessprocessdurationnotificationtemplate
  notification_receivers_types  VARCHAR(1)[]
```

### 3.8 bp_businessprocessfield — поля данных процесса

Динамические поля, которые заполняются в ходе процесса.

```
Поля:
  id                    INTEGER PRIMARY KEY
  data                  JSONB    — структура поля: {name, type, default_value, ...}
  business_process_id   FK → bp_businessprocess
  is_required           BOOLEAN  — обязательное поле
  is_variable           BOOLEAN  — переменная (используется в условиях)
  model_field           VARCHAR(150)  — связь с реальным полем Deal/Company
  created_at, updated_at
```

### 3.9 bp_draggable — полиморфная база для сущностей процесса

**Это важно!** `deal_deal` и другие сущности, которые "двигаются" по этапам процесса, наследуют `bp_draggable`.

```
bp_draggable:
  id                    INTEGER PRIMARY KEY
  current_stage_id      FK → bp_stagecomponent  — текущий этап
  polymorphic_ctype_id  FK → django_content_type — реальный тип (Deal, Company, etc.)
  bp_id                 FK → bp_businessprocess  — в рамках какого процесса

bp_draggablestatus (статусы для сущностей):
  id                    INTEGER PRIMARY KEY
  name                  VARCHAR(1000)
  color                 VARCHAR(250)
  polymorphic_ctype_id  FK → django_content_type
  created_at, updated_at
```

---

## 4. Модуль Сделок (deal_*)

### 4.1 deal_deal — главная таблица сделок

**НАСЛЕДУЕТ** `bp_draggable`. Коммерческая сделка или проект продаж.

```
Поля:
  draggable_ptr_id          FK → bp_draggable (PRIMARY KEY, полиморфное наследование)
  name                      VARCHAR(250)     — название сделки
  description               TEXT
  slug                      VARCHAR(50) UNIQUE
  sum                       DECIMAL(12,2)    — сумма сделки (может быть NULL)
  deal_number               INTEGER UNIQUE   — порядковый номер (69, 74, 48...)
  registration_number       VARCHAR(15) UNIQUE  — рег. номер вида К69-24
  is_draft                  BOOLEAN          — черновик
  has_order                 BOOLEAN          — есть ли заказ
  is_win                    BOOLEAN          — сделка выиграна
  vertical_order_number     INTEGER          — позиция в Kanban-колонке
  status_id                 FK → deal_dealstatus  — текущий статус
  author_id                 FK → staff_businessrole  — создатель сделки
  contact_id                FK → staff_contact   — контактное лицо
  deal_profit_calculator_id FK → (таблица расчёта прибыли)
  last_update_analytics     TIMESTAMP WITH TIME ZONE
  created_at, updated_at
```

**Связи:**
- M2M: `companies` → `deal_company` (через `deal_interactionform`) — компании-участники
- M2M: `leaders` → `staff_businessrole` — ответственные менеджеры
- M2M: `contractors` → `staff_businessrole` — исполнители
- M2M: `financial_operations` — финансовые операции по сделке
- M2M: `tags` → `deal_tag` — теги
- M2M: `attachments` → `filemanager_file` — прикреплённые файлы

**Реальные сделки Chemico (из БД):**
```
Рег.номер | Название сделки                                              | Автор
К69-24    | Cделка_АРТ-ГРУПП_Ячейка для титрования LDC_ГПН-Терминал   | id=10
К74-24    | ИНТЕХ_Хроматограф_ЛЛК-Интернешнл 04.12.2024                | id=10
К48-25    | Jiangsu Yongcheng_мол. сита 13X_МНПЗ_28.08.2025             | id=8
```

**Формат регистрационного номера:** `КNN-YY` где NN — порядковый номер, YY — год.

### 4.2 deal_dealstatus — статусы воронки

```
Наследует bp_draggablestatus

Статусы Chemico:
  - "Активная"         — сделка в работе
  - "Черновик"         — не готова
  - "Завершена"        — успешно закрыта
  - "В архиве"         — неактивная, архивная сделка
```

### 4.3 deal_company — компании/контрагенты

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)   — название компании
```

**Связь сделки с компаниями:**
```sql
-- Компании, связанные со сделкой
SELECT dc.name as company
FROM deal_deal d
JOIN deal_interactionform di ON d.draggable_ptr_id = di.deal_id
JOIN deal_company dc ON di.company_id = dc.id
WHERE d.registration_number = 'К69-24';
```

### 4.4 deal_interactionform — связующая таблица Deal ↔ Company

```
Поля:
  id          INTEGER PRIMARY KEY
  deal_id     FK → deal_deal
  company_id  FK → deal_company
```

### 4.5 deal_tag — теги для категоризации сделок

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)
  created_at, updated_at
```

### 4.6 deal_dealduration — сроки по сделке

```
Поля:
  id                    INTEGER PRIMARY KEY
  duration              INTERVAL         — срок
  recommended_duration  INTERVAL
  end_time              TIMESTAMP WITH TIME ZONE
  deal_id               FK → deal_deal
  parent_id             FK → self        — может быть иерархия
  recommended_with_duration  BOOLEAN
  with_duration         BOOLEAN
  created_at, updated_at
```

---

## 5. Права и Разрешения (staff_businesspermission*)

### 5.1 staff_businesspermission

```
Поля:
  id        INTEGER PRIMARY KEY
  name      VARCHAR(250)
  parent_id FK → self   — иерархия прав
  group_id  FK → staff_businesspermissiongroup
  created_at, updated_at
```

### 5.2 staff_businesspermissiongroup

```
Поля:
  id    INTEGER PRIMARY KEY
  name  VARCHAR(250)

Связи:
  M2M: business_roles → staff_businessrole (через staff_businesspermissiongrouptobusinessrole)
```

**Смысл:** Группы прав назначаются ролям. Роли назначаются сотрудникам. Так реализована ролевая модель доступа.

---

## 6. Финансовый модуль (deal_profit_calculator_*)

### 6.1 Таблица финансовых операций

Финансовые операции привязаны к сделкам и описывают поступления/расходы.

```sql
-- Финансовые операции по сделке
SELECT fo.id, fo.name, fo.sum, fo.type
FROM deal_deal d
JOIN deal_deal_financial_operations ddfo ON d.draggable_ptr_id = ddfo.deal_id
JOIN deal_profit_calculator_financialoperation fo ON ddfo.financialoperation_id = fo.id
WHERE d.registration_number = 'К69-24';
```

---

## 7. Ключевые SQL-запросы

### 7.1 Список всех сотрудников с должностями

```sql
SELECT
    au.id,
    au.username,
    au.first_name || ' ' || au.last_name AS full_name,
    au.email,
    sp.position,
    sp.is_administrator,
    sp.is_online
FROM auth_user au
JOIN staff_profile sp ON au.id = sp.user_id
WHERE au.is_active = true
ORDER BY au.last_name, au.first_name;
```

### 7.2 Активные сделки с ответственными и компаниями

```sql
SELECT
    d.registration_number AS deal_number,
    d.name AS deal_name,
    d.sum,
    ds.name AS status,
    br_author.name AS author,
    sc.name AS contact_person,
    string_agg(DISTINCT dc.name, ', ') AS companies,
    string_agg(DISTINCT br_lead.name, ', ') AS leaders
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
JOIN staff_businessrole br_author ON d.author_id = br_author.id
LEFT JOIN staff_contact sc ON d.contact_id = sc.id
LEFT JOIN deal_interactionform di ON d.draggable_ptr_id = di.deal_id
LEFT JOIN deal_company dc ON di.company_id = dc.id
LEFT JOIN deal_deal_leaders ddl ON d.draggable_ptr_id = ddl.deal_id
LEFT JOIN staff_businessrole br_lead ON ddl.businessrole_id = br_lead.id
WHERE ds.name NOT IN ('В архиве', 'Черновик')
GROUP BY d.draggable_ptr_id, d.registration_number, d.name, d.sum, ds.name, br_author.name, sc.name
ORDER BY d.created_at DESC;
```

### 7.3 Сделки по конкретному сотруднику (ответственный)

```sql
SELECT
    d.registration_number,
    d.name,
    d.sum,
    ds.name AS status,
    d.created_at
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
JOIN deal_deal_leaders ddl ON d.draggable_ptr_id = ddl.deal_id
JOIN staff_businessrole br ON ddl.businessrole_id = br.id
JOIN staff_profile_business_roles spbr ON br.id = spbr.businessrole_id
JOIN staff_profile sp ON spbr.profile_id = sp.id
JOIN auth_user au ON sp.user_id = au.id
WHERE au.username = 'LotovD'
  AND ds.name NOT IN ('В архиве')
ORDER BY d.created_at DESC;
```

### 7.4 Сделки по конкретной компании (контрагенту)

```sql
SELECT
    d.registration_number,
    d.name,
    d.sum,
    ds.name AS status,
    d.created_at
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
JOIN deal_interactionform di ON d.draggable_ptr_id = di.deal_id
JOIN deal_company dc ON di.company_id = dc.id
WHERE dc.name ILIKE '%ИНТЕХ%'
ORDER BY d.created_at DESC;
```

### 7.5 Количество сделок по статусам

```sql
SELECT
    ds.name AS status,
    COUNT(d.draggable_ptr_id) AS count,
    SUM(d.sum) AS total_sum
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
GROUP BY ds.name
ORDER BY count DESC;
```

### 7.6 Количество активных сделок по ответственным

```sql
SELECT
    br.name AS responsible_person,
    COUNT(DISTINCT d.draggable_ptr_id) AS active_deals
FROM staff_businessrole br
JOIN deal_deal_leaders ddl ON br.id = ddl.businessrole_id
JOIN deal_deal d ON ddl.deal_id = d.draggable_ptr_id
JOIN deal_dealstatus ds ON d.status_id = ds.id
WHERE ds.name = 'Активная'
  AND br.is_staff = true
GROUP BY br.name
ORDER BY active_deals DESC;
```

### 7.7 Все бизнес-процессы с количеством этапов

```sql
SELECT
    bp.id,
    bp.name,
    bp.type,
    g.name AS bp_group,
    COUNT(bpc.id) AS stage_count
FROM bp_businessprocess bp
LEFT JOIN bp_group g ON bp.group_id = g.id
LEFT JOIN bp_businessprocesscomponent bpc ON bp.id = bpc.business_process_id
GROUP BY bp.id, bp.name, bp.type, g.name
ORDER BY bp.id;
```

### 7.8 Сделки с датами создания за период

```sql
SELECT
    d.registration_number,
    d.name,
    d.sum,
    ds.name AS status,
    d.created_at::date AS created_date
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
WHERE d.created_at >= NOW() - INTERVAL '30 days'
ORDER BY d.created_at DESC;
```

### 7.9 Все контрагенты (компании)

```sql
SELECT
    dc.id,
    dc.name,
    COUNT(DISTINCT di.deal_id) AS deal_count
FROM deal_company dc
LEFT JOIN deal_interactionform di ON dc.id = di.company_id
GROUP BY dc.id, dc.name
ORDER BY deal_count DESC;
```

### 7.10 Сотрудники с ролями и отделами

```sql
SELECT
    au.username,
    au.first_name || ' ' || au.last_name AS full_name,
    sp.position,
    string_agg(DISTINCT br.name, ', ') AS roles
FROM auth_user au
JOIN staff_profile sp ON au.id = sp.user_id
LEFT JOIN staff_profile_business_roles spbr ON sp.id = spbr.profile_id
LEFT JOIN staff_businessrole br ON spbr.businessrole_id = br.id AND br.is_staff = false
WHERE au.is_active = true
GROUP BY au.id, au.username, au.first_name, au.last_name, sp.position
ORDER BY au.last_name;
```

### 7.11 Выгрузка всех сделок в полном виде (до 1000 строк)

```sql
SELECT
    d.registration_number AS "Рег. номер",
    d.name AS "Название сделки",
    d.sum AS "Сумма",
    ds.name AS "Статус",
    br_author.name AS "Автор",
    sc.name AS "Контактное лицо",
    string_agg(DISTINCT dc.name, '; ') AS "Компании",
    string_agg(DISTINCT br_lead.name, '; ') AS "Ответственные",
    d.created_at::date AS "Дата создания",
    d.updated_at::date AS "Дата обновления"
FROM deal_deal d
JOIN deal_dealstatus ds ON d.status_id = ds.id
JOIN staff_businessrole br_author ON d.author_id = br_author.id
LEFT JOIN staff_contact sc ON d.contact_id = sc.id
LEFT JOIN deal_interactionform di ON d.draggable_ptr_id = di.deal_id
LEFT JOIN deal_company dc ON di.company_id = dc.id
LEFT JOIN deal_deal_leaders ddl ON d.draggable_ptr_id = ddl.deal_id
LEFT JOIN staff_businessrole br_lead ON ddl.businessrole_id = br_lead.id
GROUP BY d.draggable_ptr_id, d.registration_number, d.name, d.sum, ds.name,
         br_author.name, sc.name, d.created_at, d.updated_at
ORDER BY d.created_at DESC
LIMIT 1000;
```

---

## 8. Полная карта связей между таблицами

```
auth_user (1) ──────────────────── (1) staff_profile
                                           │
                           ┌───────────────┤
                           │               │
              staff_profile_business_roles │
                           │               │
                           ▼               │
                staff_businessrole ◄───────┘
                     │
        ┌────────────┼─────────────────┐
        │            │                 │
        ▼            ▼                 ▼
deal_deal_leaders  bp_businessprocess  bp_swimlane_leaders
(M2M)             .author_id, .owner_id .leaders (M2M)
        │
        ▼
    deal_deal (наследует bp_draggable)
        │
        ├── FK: status_id → deal_dealstatus
        ├── FK: contact_id → staff_contact
        ├── M2M: companies → deal_company (через deal_interactionform)
        ├── M2M: contractors → staff_businessrole
        ├── M2M: tags → deal_tag
        └── M2M: attachments → filemanager_file


bp_businessprocess
        │
        ├── FK: group_id → bp_group
        ├── 1:M → bp_businessprocesscomponent
        │          ├── bp_stage (этапы)
        │          └── bp_gateway (шлюзы)
        ├── 1:M → bp_businessprocessduration
        ├── 1:M → bp_businessprocessfield
        └── M2M → bp_swimlane
```

---

## 9. Особенности и частые ошибки при запросах

### 9.1 deal_deal использует составной ключ через bp_draggable

Первичный ключ `deal_deal` — это `draggable_ptr_id`, а не просто `id`. При JOIN всегда используй:
```sql
-- ПРАВИЛЬНО:
JOIN deal_deal_leaders ddl ON d.draggable_ptr_id = ddl.deal_id

-- НЕПРАВИЛЬНО:
JOIN deal_deal_leaders ddl ON d.id = ddl.deal_id  -- поля 'id' нет в deal_deal!
```

### 9.2 Сотрудники через businessrole, не через auth_user напрямую

Ответственные за сделку хранятся через `staff_businessrole`, а не через `auth_user`. Чтобы найти человека, нужна цепочка:
```
deal_deal_leaders → staff_businessrole → staff_profile_business_roles → staff_profile → auth_user
```

### 9.3 Статусы сделок через deal_dealstatus, не текстом

Статус сделки — это FK на `deal_dealstatus`, не текстовое поле. Фильтруй:
```sql
WHERE ds.name = 'Активная'  -- через JOIN с deal_dealstatus
```

### 9.4 Компании через interactionform

Компании связаны со сделкой через промежуточную таблицу `deal_interactionform`:
```
deal_deal → deal_interactionform → deal_company
```

### 9.5 LIMIT по умолчанию

При выгрузке больших таблиц всегда добавляй `LIMIT 1000` если пользователь явно не просил больше.

---

## 10. Бизнес-контекст Chemico

**Компания Chemico** — торговля аналитическим и лабораторным оборудованием, химическими реактивами.

**Типичные клиенты (контрагенты):**
- Нефтегазовые компании (ГПН-Терминал, МНПЗ, ЛЛК-Интернешнл)
- Химические и производственные предприятия
- Исследовательские организации

**Типичные сделки:**
- Поставка лабораторного оборудования (хроматографы, анализаторы)
- Молекулярные сита, реактивы
- Оборудование для контроля качества

**Формат названия сделки:** `[Компания]_[Продукт]_[Клиент]_[Дата]`
Пример: `ИНТЕХ_Хроматограф_ЛЛК-Интернешнл 04.12.2024`

**Регистрационный номер:** `КNN-YY`
- К = коммерческое предложение/сделка
- NN = порядковый номер
- YY = год (24 = 2024, 25 = 2025)

---

## 11. Таблицы для служебных нужд (не трогать в запросах)

| Таблица | Назначение |
|---------|-----------|
| `django_migrations` | Список применённых миграций Django |
| `django_content_type` | Реестр всех моделей (нужен для полиморфизма) |
| `django_session` | Сессии пользователей |
| `django_admin_log` | Лог действий в Django Admin |
| `auth_permission` | Права доступа Django |
| `auth_group` | Группы пользователей Django |
| `*_history` | Таблицы аудита изменений (simple_history) |
| `notification_*` | Служебные уведомления |

---

## 12. Примеры вопросов и готовые ответы

**"Сколько всего сделок?"**
```sql
SELECT COUNT(*) FROM deal_deal;
```

**"Активные сделки"**
```sql
SELECT d.registration_number, d.name, ds.name as status
FROM deal_deal d JOIN deal_dealstatus ds ON d.status_id = ds.id
WHERE ds.name = 'Активная' ORDER BY d.created_at DESC;
```

**"Топ-10 сделок по сумме"**
```sql
SELECT d.registration_number, d.name, d.sum, ds.name as status
FROM deal_deal d JOIN deal_dealstatus ds ON d.status_id = ds.id
WHERE d.sum IS NOT NULL
ORDER BY d.sum DESC LIMIT 10;
```

**"Сколько сотрудников?"**
```sql
SELECT COUNT(*) FROM auth_user WHERE is_active = true AND username != 'adminbp';
```

**"Список всех контрагентов"**
```sql
SELECT id, name FROM deal_company ORDER BY name;
```

**"Сделки за последний месяц"**
```sql
SELECT d.registration_number, d.name, d.sum, ds.name as status, d.created_at::date
FROM deal_deal d JOIN deal_dealstatus ds ON d.status_id = ds.id
WHERE d.created_at >= NOW() - INTERVAL '30 days'
ORDER BY d.created_at DESC;
```

**"Все бизнес-процессы"**
```sql
SELECT id, name, type FROM bp_businessprocess ORDER BY id;
```
'''.strip()


# ── Создать или обновить статью ──────────────────────────────────────────────

print(f'Creating/updating article: {SLUG}')

# Найти или создать пространство (берём первое Space в системе)
from recordings.models import Space
space = Space.objects.first()

article, created = WikiArticle.objects.get_or_create(
    slug=SLUG,
    defaults=dict(
        title=TITLE,
        content=CONTENT,
        parent=None,
        space=space,
    )
)

if not created:
    article.title = TITLE
    article.content = CONTENT
    article.space = space
    article.save()
    print(f'Updated: {SLUG}')
else:
    print(f'Created: {SLUG}')

print('Indexing chunks...')
ok = index_wiki_article(article)
print(f'Indexing {"OK" if ok else "FAILED"}: pk={article.pk} slug={article.slug}')
print(f'Article URL: /kb/{SLUG}/')
