"""
Chemico DB Agent — LangChain SQL Agent с Excel-экспортом.

Основные функции:
  ask(question, chat_id=None) → {'text': str, 'excel_path': str|None}

Инструменты агента:
  - sql_db_list_tables       — список таблиц
  - sql_db_schema            — схема таблицы + примеры
  - sql_db_query             — выполнить SELECT
  - sql_db_query_checker     — проверить SQL перед выполнением
  - export_to_excel          — выполнить SQL и сохранить xlsx

Подключение: postgresql+psycopg://chemico:chemico@chemico_db:5432/chemico

Кэш: SQLDatabase и Agent кэшируются на уровне процесса.
  Сброс кэша: invalidate_cache() или вызывается автоматически при смене провайдера/модели.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Thread-local хранилище для chat_id/token текущего запроса.
# Обходит проблему кэширования агента: notify_progress бакается в кэш агента,
# но chat_id меняется для каждого пользователя — читаем из thread-local.
_request_local = threading.local()

# URL базы — можно переопределить через env
CHEMICO_DB_URL = os.environ.get(
    'CHEMICO_DB_URL',
    'postgresql+psycopg://chemico:chemico@chemico_db:5432/chemico',
)

# Системный промпт с бизнес-контекстом
_SYSTEM_PROMPT = """Ты аналитический ассистент для базы данных клиента Chemico на платформе BusinessPad.

ВАЖНО: Всегда используй инструмент notify_progress чтобы сообщать пользователю о ходе работы.
Обязательные этапы уведомления:
- Перед чтением шаблона: '📖 Читаю шаблон...'
- Перед SQL-запросами: '🔍 Запрашиваю данные из базы...'
- Перед созданием файла: '📊 Формирую Excel файл...'
- После завершения: '✅ Готово!'
Без этих уведомлений пользователь видит пустой экран и думает что бот завис.

База данных содержит бизнес-процессы, сделки, CRM, сотрудников, финансы.
Ключевые модули: bp_ (бизнес-процессы), deal_ (сделки), crm_ (CRM), staff_ (сотрудники).

══════════════ ЖЁСТКИЕ ЗАПРЕТЫ — НАРУШЕНИЕ ВЫЗОВЕТ ОШИБКУ SQL ══════════════

❌ ЗАПРЕЩЕНО: SQL с комментариями-заглушками вместо реальных значений:
   WHERE x IN ('К5-25', -- добавьте остальные...  )  → СИНТАКСИЧЕСКАЯ ОШИБКА!
   Если нужны значения из Excel — прочитай файл через read_excel_template и вставь ВСЕ значения.
   Никогда не оставляй -- placeholder комментарии внутри SQL-выражений.

❌ ЗАПРЕЩЕНО: JOIN deal_company ON deal_company.draggable_ptr_id = fo.counterparty
   ПРИЧИНА: fo.counterparty — VARCHAR(250), draggable_ptr_id — INTEGER. Несовместимые типы → ошибка psycopg.
   ✅ ПРАВИЛЬНО: просто используй fo.counterparty AS "контрагент" — это уже текст, JOIN не нужен.

❌ ЗАПРЕЩЕНО: fop.deal_id или fo.deal_id (поля не существует в financialoperation!)
   ✅ ПРАВИЛЬНО: связь через M2M:
      JOIN deal_deal_financial_operations dfo ON dfo.financialoperation_id = fo.id
      JOIN deal_deal d ON d.draggable_ptr_id = dfo.deal_id

❌ ЗАПРЕЩЕНО: WHERE d.deal_number = 'К5-25' (deal_number — INTEGER, строки там нет!)
   ✅ ПРАВИЛЬНО: WHERE d.registration_number = 'К5-25'

Прочие обязательные правила схемы:
- deal_deal.draggable_ptr_id — первичный ключ сделки (НЕ id!)
- deal_company.draggable_ptr_id — первичный ключ компании (НЕ id!)

══════════════════════════════════════════════════════════════════════════════

Правила:
- ВСЕГДА пиши только SELECT-запросы. Никаких INSERT/UPDATE/DELETE/DROP.
- Используй LIMIT при запросах больших таблиц (не более 1000 строк без явного запроса).
- Если вопрос на русском — отвечай на русском.
- Для числовых итогов форматируй красиво (пробелы как разделители тысяч).
- Если данных нет — явно скажи об этом.
- Когда пользователь просит выгрузку, таблицу, отчёт или Excel-файл — ОБЯЗАТЕЛЬНО вызови инструмент export_to_excel.
  НИКОГДА не придумывай и не составляй URL файлов самостоятельно. URL придёт в ответе инструмента.
- Когда пользователь просит Excel с НЕСКОЛЬКИМИ листами (например «два листа: План и Реализация») —
  ОБЯЗАТЕЛЬНО используй инструмент export_to_excel_multisheet.
  Передай JSON-массив: [{"sheet": "План", "sql": "SELECT ..."}, {"sheet": "Реализация", "sql": "SELECT ..."}]
  Каждый лист — отдельный SELECT-запрос. НЕ делай UNION ALL для разных листов.
- Когда пользователь говорит «по шаблону», «используй шаблон», «по структуре файла» — ДВУХШАГОВЫЙ процесс:
  ШАГ 1: вызови list_excel_templates, затем get_wiki_article для нужного slug.
  В шаблоне есть раздел «Уточняющие вопросы» — ОБЯЗАТЕЛЬНО задай все эти вопросы пользователю
  и ОСТАНОВИСЬ. НЕ делай SQL-запросы и не создавай Excel на этом шаге.
  ШАГ 2 (следующее сообщение пользователя): пользователь ответил на вопросы →
  теперь читай шаблон снова (он уже в истории), строй SQL по инструкции шаблона и делай выгрузку.
- Если в истории диалога ты уже задавал уточняющие вопросы по шаблону и пользователь ответил —
  сразу выполняй выгрузку без повторных вопросов.
"""

# ── Кэш ────────────────────────────────────────────────────────────────────
_db_cache = None           # langchain SQLDatabase (переиспользуем между вызовами)
_agent_cache = None        # (agent_executor, cache_key)


def invalidate_cache():
    """Принудительный сброс кэша агента (при смене провайдера/модели/KB-режима)."""
    global _db_cache, _agent_cache
    _db_cache = None
    _agent_cache = None
    logger.info('chemico_agent: кэш сброшен')


# Только нужные таблицы — вместо 292. Ускоряет агента в 5-10x:
# меньше schema-вызовов, точнее SQL с первого раза.
_RELEVANT_TABLES = [
    # Сделки
    'deal_deal',
    'deal_dealstatus',
    'deal_company',
    'deal_interactionform',
    'deal_deal_leaders',
    'deal_deal_contractors',
    'deal_deal_tags',
    'deal_tag',
    # Финансы
    'deal_deal_financial_operations',
    'deal_profit_calculator_financialoperation',
    'deal_profit_calculator_currencyunit',
    'deal_profit_calculator_measurementunit',
    'deal_profit_calculator_productunit',
    # Сотрудники
    'auth_user',
    'staff_profile',
    'staff_profile_business_roles',
    'staff_businessrole',
    'staff_businessroletype',
    'staff_department',
    'staff_contact',
    # Бизнес-процессы
    'bp_businessprocess',
    'bp_businessprocesscomponent',
    'bp_draggable',
    'bp_draggablestatus',
    'bp_group',
    'bp_stage',
    'bp_swimlane',
]


def _get_db():
    """Возвращает кэшированный LangChain SQLDatabase для chemico."""
    global _db_cache
    if _db_cache is None:
        logger.info('chemico_agent: инициализация SQLDatabase (%d таблиц)', len(_RELEVANT_TABLES))
        from langchain_community.utilities import SQLDatabase
        _db_cache = SQLDatabase.from_uri(
            CHEMICO_DB_URL,
            sample_rows_in_table_info=1,
            include_tables=_RELEVANT_TABLES,
            max_string_length=300,
        )
        logger.info('chemico_agent: SQLDatabase готова')
    return _db_cache


def _get_export_dir():
    """Возвращает путь к папке экспорта, создаёт если нет."""
    from django.conf import settings
    export_dir = settings.MEDIA_ROOT / 'db_exports'
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _make_excel_tool(db):
    """Создаёт инструмент export_to_excel как LangChain Tool."""
    from langchain_core.tools import tool
    import pandas as pd

    @tool
    def export_to_excel(sql: str) -> str:
        """
        Выполни SQL-запрос и сохрани результат в Excel-файл (.xlsx).
        Возвращает URL для скачивания файла. Используй этот инструмент когда пользователь
        просит таблицу, отчёт, выгрузку или файл Excel.

        Args:
            sql: SELECT-запрос для выполнения
        """
        try:
            from sqlalchemy import text, create_engine
            engine = create_engine(CHEMICO_DB_URL)
            with engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)

            if df.empty:
                return 'Запрос вернул пустой результат — Excel-файл не создан.'

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            fname = f'chemico_export_{ts}.xlsx'
            fpath = _get_export_dir() / fname

            with pd.ExcelWriter(str(fpath), engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Данные')
                ws = writer.sheets['Данные']
                for col in ws.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

            from django.conf import settings
            site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
            download_url = f'{site_url}/db-export/{fname}'

            return f'EXCEL_FILE:{fpath}:{len(df)} строк, {len(df.columns)} колонок:{download_url}'

        except Exception as e:
            logger.exception('export_to_excel error')
            return f'Ошибка при создании Excel: {e}'

    return export_to_excel


def _make_excel_multisheet_tool():
    """Создаёт инструмент export_to_excel_multisheet — Excel с несколькими листами."""
    from langchain_core.tools import tool
    import pandas as pd

    @tool
    def export_to_excel_multisheet(sheets_json: str) -> str:
        """
        Создать Excel-файл с несколькими листами. Каждый лист — отдельный SQL-запрос.
        Используй этот инструмент когда пользователь просит Excel с несколькими листами
        (например «сделай два листа: План и Реализация»).

        Args:
            sheets_json: JSON-массив объектов вида [{"sheet": "Название листа", "sql": "SELECT ..."}].
            Например: [{"sheet": "План", "sql": "SELECT ..."}, {"sheet": "Реализация", "sql": "SELECT ..."}]
        """
        import json
        try:
            sheets = json.loads(sheets_json)
        except Exception as e:
            return f'Ошибка парсинга JSON: {e}. Передай корректный JSON-массив.'

        if not sheets or not isinstance(sheets, list):
            return 'Передай непустой JSON-массив листов.'

        try:
            from sqlalchemy import text, create_engine
            engine = create_engine(CHEMICO_DB_URL)

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            fname = f'chemico_export_{ts}.xlsx'
            fpath = _get_export_dir() / fname

            total_rows = 0
            with pd.ExcelWriter(str(fpath), engine='openpyxl') as writer:
                for sheet_def in sheets:
                    sheet_name = str(sheet_def.get('sheet', 'Лист'))[:31]
                    sql = sheet_def.get('sql', '')
                    if not sql:
                        continue
                    with engine.connect() as conn:
                        df = pd.read_sql(text(sql), conn)
                    df.to_excel(writer, index=False, sheet_name=sheet_name)
                    ws = writer.sheets[sheet_name]
                    for col in ws.columns:
                        max_len = max(len(str(cell.value or '')) for cell in col)
                        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)
                    total_rows += len(df)

            from django.conf import settings
            site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
            download_url = f'{site_url}/db-export/{fname}'

            sheet_names = ', '.join(s.get('sheet', '?') for s in sheets)
            return f'EXCEL_FILE:{fpath}:{total_rows} строк, листы: {sheet_names}:{download_url}'

        except Exception as e:
            logger.exception('export_to_excel_multisheet error')
            return f'Ошибка при создании Excel: {e}'

    return export_to_excel_multisheet


def _make_wiki_article_tool():
    """Инструмент get_wiki_article — прочитать содержимое wiki-статьи по slug."""
    from langchain_core.tools import tool

    @tool
    def get_wiki_article(slug: str) -> str:
        """
        Прочитать содержимое wiki-статьи по её slug.
        Используй для чтения структуры Excel-шаблонов (список шаблонов — из list_excel_templates).

        Args:
            slug: slug статьи, например 'chemico-tpl-report-abc123'
        """
        try:
            from wiki_kb.models import WikiArticle
            art = WikiArticle.objects.filter(slug=slug, is_deleted=False).first()
            if not art:
                return f'Статья со slug "{slug}" не найдена.'
            return f'# {art.title}\n\n{art.content[:4000]}'
        except Exception as e:
            return f'Ошибка при чтении статьи: {e}'

    return get_wiki_article


def _make_template_tool():
    """Создаёт инструмент list_excel_templates — показывает сохранённые Excel-шаблоны."""
    from langchain_core.tools import tool

    @tool
    def list_excel_templates() -> str:
        """
        Показать список Excel-шаблонов, сохранённых пользователем в базе знаний.
        Каждый шаблон содержит структуру колонок и примеры данных из загруженных пользователем файлов.
        Используй этот инструмент когда пользователь говорит «по шаблону», «используй шаблон»,
        или когда нужно построить запрос по структуре файла который присылал пользователь.
        """
        try:
            from wiki_kb.models import WikiArticle
            parent = WikiArticle.objects.filter(slug='chemico-query-templates', is_deleted=False).first()
            if not parent:
                return 'Шаблонов пока нет. Отправь Excel-файл с подписью "задай шаблон" чтобы создать первый.'
            children = WikiArticle.objects.filter(parent=parent, is_deleted=False).order_by('-id')[:20]
            if not children:
                return 'Шаблонов пока нет. Отправь Excel-файл с подписью "задай шаблон" чтобы создать первый.'
            lines = ['Сохранённые Excel-шаблоны:\n']
            for art in children:
                lines.append(f'- [{art.slug}] {art.title}')
            lines.append('\nЧтобы посмотреть структуру шаблона используй инструмент get_article с нужным slug.')
            return '\n'.join(lines)
        except Exception as e:
            return f'Ошибка при получении шаблонов: {e}'

    return list_excel_templates


def _make_notify_tool():
    """
    Инструмент notify_progress — отправить пользователю уведомление об этапе работы.
    Читает chat_id/token из thread-local (_request_local), который задаётся в ask()
    перед каждым вызовом агента. Это позволяет кэшировать агент и при этом
    правильно доставлять уведомления каждому пользователю в его чат.
    """
    from langchain_core.tools import tool

    @tool
    def notify_progress(stage: str) -> str:
        """
        Отправить пользователю сообщение о текущем этапе работы агента.
        Вызывай ОБЯЗАТЕЛЬНО перед каждым значимым действием чтобы пользователь видел прогресс.
        Примеры: '📖 Читаю шаблон...', '🔍 Запрашиваю данные из БД...', '📊 Формирую Excel файл...', '✅ Готово!'

        Args:
            stage: короткое описание текущего этапа (1-2 строки, можно с эмодзи)
        """
        chat_id = getattr(_request_local, 'chat_id', None)
        token = getattr(_request_local, 'token', None)
        if not chat_id:
            return 'ok'
        try:
            from recordings.telegram_service import send_message
            send_message(chat_id, stage, token=token)
        except Exception as e:
            logger.warning('notify_progress error: %s', e)
        return 'ok'

    return notify_progress


def build_agent():
    """
    Возвращает кэшированный LangChain SQL Agent.
    Пересоздаёт только при смене провайдера/модели/KB-режима.
    chat_id/token не передаются — notify_progress читает их из _request_local (thread-safe).
    """
    global _agent_cache

    from langchain_community.agent_toolkits.sql.base import create_sql_agent
    from chemico_agent.llm import get_langchain_llm, get_provider_info
    from chemico_agent.knowledge import get_wiki_context, auto_ensure_wiki_article
    from recordings.models import SystemConfig

    info = get_provider_info()
    kb_mode = SystemConfig.get('chemico_kb_mode', '1')

    # Обновляем wiki-статью схемы если устарела (однократно при пересборке агента)
    auto_ensure_wiki_article()

    # Загружаем wiki-контекст и включаем его хеш в cache_key,
    # чтобы при обновлении статьи агент автоматически пересобирался
    wiki_ctx = get_wiki_context()
    wiki_hash = hash(wiki_ctx)
    cache_key = (info['provider'], info['model'], kb_mode, wiki_hash)

    if _agent_cache and _agent_cache[1] == cache_key:
        logger.info('chemico_agent: используем кэшированный агент (%s/%s, kb=%s, whash=%s)', *cache_key)
        return _agent_cache[0]

    logger.info('chemico_agent: пересборка агента (provider=%s model=%s kb=%s wiki_hash=%s)', *cache_key)
    llm = get_langchain_llm(temperature=0.0)
    db  = _get_db()
    provider = info['provider']
    extra_tools = [_make_excel_tool(db), _make_excel_multisheet_tool(), _make_template_tool(), _make_wiki_article_tool(), _make_notify_tool()]

    # OpenAI / grok / gonka — используем OPENAI_TOOLS (нативный function calling).
    # Это надёжнее ReAct: нет проблем с парсингом формата, нет петель "Invalid Format".
    # Anthropic — ReAct (не поддерживает OpenAI tools format через create_sql_agent).
    if provider in ('openai', 'grok', 'gonka'):
        # Системный промпт передаём через suffix (prefix не поддерживается в OPENAI_TOOLS)
        import re as _re
        raw_suffix = _SYSTEM_PROMPT + wiki_ctx
        suffix = _re.sub(r'\{([^}]*)\}', r'[\1]', raw_suffix)
        agent_executor = create_sql_agent(
            llm=llm,
            db=db,
            agent_type='openai-tools',
            extra_tools=extra_tools,
            suffix=suffix,
            verbose=True,
            max_iterations=20,
            max_execution_time=300,
            agent_executor_kwargs={
                'return_intermediate_steps': True,
            },
        )
    else:
        # ReAct fallback для Anthropic и других
        import re as _re
        raw_prefix = _SYSTEM_PROMPT + wiki_ctx
        prefix = _re.sub(r'\{([^}]*)\}', r'[\1]', raw_prefix)
        agent_executor = create_sql_agent(
            llm=llm,
            db=db,
            extra_tools=extra_tools,
            prefix=prefix,
            verbose=True,
            max_iterations=20,
            max_execution_time=300,
            agent_executor_kwargs={
                'handle_parsing_errors': True,
                'return_intermediate_steps': True,
            },
        )
    _agent_cache = (agent_executor, cache_key)
    return agent_executor


_HISTORY_LIMIT = 10  # последних сообщений (5 пар user/assistant)


def _load_history(chat_id: int, bot_id) -> str:
    """Возвращает строку с последними N сообщениями из BotChatHistory."""
    try:
        from recordings.models import BotChatHistory
        msgs = list(
            BotChatHistory.objects.filter(chat_id=chat_id, bot_id=bot_id)
            .order_by('-created_at')[:_HISTORY_LIMIT]
        )
        msgs.reverse()
        if not msgs:
            return ''
        lines = []
        for m in msgs:
            role_label = 'Пользователь' if m.role == 'user' else 'Ассистент'
            lines.append(f'{role_label}: {m.content[:500]}')
        return '\n'.join(lines)
    except Exception as e:
        logger.warning('chemico_agent: не удалось загрузить историю: %s', e)
        return ''


def _save_history(chat_id: int, bot_id, role: str, content: str) -> None:
    try:
        from recordings.models import BotChatHistory
        BotChatHistory.objects.create(chat_id=chat_id, bot_id=bot_id, role=role, content=content[:4000])
    except Exception as e:
        logger.warning('chemico_agent: не удалось сохранить историю: %s', e)


def ask(question: str, chat_id: int | None = None, bot_id=None, token: str | None = None) -> dict:
    """
    Главная точка входа.

    Returns:
        {
          'text':       str,        # текстовый ответ
          'excel_path': str | None, # путь к .xlsx если был экспорт
          'provider':   str,        # openai | anthropic | grok | gonka
          'model':      str,
        }
    """
    from chemico_agent.llm import get_provider_info

    info = get_provider_info()
    logger.info('chemico_agent.ask: provider=%s model=%s q=%r', info['provider'], info['model'], question[:80])

    # Устанавливаем thread-local для notify_progress (доступен из кэшированного агента)
    _request_local.chat_id = chat_id
    _request_local.token = token

    # Сохраняем вопрос в историю
    if chat_id:
        _save_history(chat_id, bot_id, 'user', question)

    # Добавляем историю к вопросу
    history_str = _load_history(chat_id, bot_id) if chat_id else ''
    if history_str:
        full_input = f'История диалога:\n{history_str}\n\nТекущий вопрос: {question}'
    else:
        full_input = question

    try:
        agent_executor = build_agent()
        result = agent_executor.invoke({'input': full_input})
        output = result.get('output', '')
    except Exception as e:
        logger.exception('chemico_agent.ask error')
        # Сбрасываем кэш — при ошибке конфигурации агента он будет пересоздан
        invalidate_cache()
        err_str = str(e)
        if 'timeout' in err_str.lower() or 'timed out' in err_str.lower():
            text = '⏱ Время ожидания ответа от LLM истекло. Попробуйте повторить или упростить запрос.'
        elif 'max_execution_time' in err_str.lower() or 'time limit' in err_str.lower():
            text = '⏱ Агент не успел ответить за отведённое время. Попробуйте более конкретный запрос.'
        else:
            text = f'❌ Ошибка агента: {err_str[:300]}'
        return {
            'text': text,
            'excel_path': None,
            **info,
        }

    # Извлекаем путь к Excel и URL из промежуточных шагов агента
    # Формат вывода инструмента: EXCEL_FILE:/path/to/file.xlsx:N строк, M колонок:https://...
    excel_path = None
    excel_url = None
    for step in result.get('intermediate_steps', []):
        # step = (AgentAction, observation_str)
        observation = step[1] if isinstance(step, (list, tuple)) and len(step) > 1 else ''
        if not isinstance(observation, str) or 'EXCEL_FILE:' not in observation:
            continue
        for part in observation.split():
            if not part.startswith('EXCEL_FILE:'):
                continue
            raw = part[len('EXCEL_FILE:'):]
            url_sep = raw.rfind(':http')
            if url_sep != -1:
                excel_url = raw[url_sep + 1:]
                raw = raw[:url_sep]
            path_sep = raw.index(':') if ':' in raw else len(raw)
            excel_path = raw[:path_sep]
            break
        if excel_path:
            break

    # Сохраняем ответ в историю
    if chat_id:
        _save_history(chat_id, bot_id, 'assistant', output)

    return {
        'text':       output,
        'excel_path': excel_path,
        'excel_url':  excel_url,
        **info,
    }
