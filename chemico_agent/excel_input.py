"""
Обработка входящих Excel-файлов от пользователя.

Режимы работы:
  1. Вопрос по файлу  — "сколько строк с суммой > 100?"
  2. Дополнение файла — "дополни данными из БД по полю ИНН"
     Агент ищет совпадения в chemico_db и добавляет колонки.

Точка входа: handle_excel_upload(chat_id, file_path, caption, token)
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime

logger = logging.getLogger(__name__)

# Ключевые фразы, которые означают "дополни файл из БД"
_ENRICH_KEYWORDS = [
    'дополни', 'обогати', 'добавь', 'подтяни', 'из базы', 'из бд',
    'enrich', 'merge', 'join', 'supplement', 'fill',
    'заполни', 'подставь', 'подтяни данные',
]

# Ключевые фразы для сохранения шаблона
_TEMPLATE_KEYWORDS = [
    'задай шаблон', 'сохрани шаблон', 'запомни шаблон', 'шаблон для запросов',
    'шаблон запроса', 'сделай шаблон', 'создай шаблон', 'template',
    'сохрани таблицу', 'запомни таблицу', 'запомни структуру',
]


def _is_enrich_request(caption: str) -> bool:
    caption_lower = caption.lower()
    return any(kw in caption_lower for kw in _ENRICH_KEYWORDS)


def _is_template_request(caption: str) -> bool:
    caption_lower = caption.lower()
    return any(kw in caption_lower for kw in _TEMPLATE_KEYWORDS)


def _generate_column_prompts(sheet_name: str, col_meta: list, llm) -> dict:
    """
    Для каждой колонки листа сгенерировать целевой промпт через LLM.
    col_meta: list of (name, dtype, examples_list)
    Возвращает dict {col_name: prompt_str}.
    """
    import json as _json
    from langchain_core.messages import HumanMessage

    cols_info = '\n'.join(
        f'- "{name}" (тип: {dtype}, примеры: {", ".join(str(e)[:30] for e in examples[:3]) or "—"})'
        for name, dtype, examples in col_meta
    )

    prompt = f"""Ты аналитик данных. Перед тобой описание колонок таблицы "{sheet_name}".

{cols_info}

Для КАЖДОЙ колонки напиши целевой промпт — 1-2 предложения, объясняющие:
1. Что содержит это поле (смысл данных)
2. Как его заполнять: откуда брать данные, какой формат, особые правила

Верни ТОЛЬКО JSON-объект вида:
{{"название_колонки": "промпт как заполнять", ...}}

Не добавляй пояснений вне JSON."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content.strip()
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return _json.loads(text.strip())
    except Exception as e:
        logger.warning('_generate_column_prompts failed: %s', e)
        return {name: f'Поле "{name}" (тип: {dtype})' for name, dtype, _ in col_meta}


def save_excel_as_template(file_path: str, original_name: str, caption: str = '', chat_id: int = None) -> dict:
    """
    Сохранить структуру Excel как шаблон в wiki.

    Для каждого листа формируется таблица с колонками и целевым промптом —
    LLM-описанием того, как заполнять каждое поле.
    """
    import pandas as pd
    from chemico_agent.llm import get_provider_info, get_langchain_llm
    from datetime import datetime as _dt
    import uuid as _uuid

    info = get_provider_info()
    llm = get_langchain_llm(temperature=0.0)

    # ── Читаем все листы ──────────────────────────────────────────────────────
    try:
        sheets: dict = pd.read_excel(file_path, sheet_name=None, engine='openpyxl')
    except Exception as e:
        return {'text': f'Не удалось прочитать Excel: {e}', 'excel_path': None, **info}

    if not sheets:
        return {'text': 'Excel-файл пустой (нет листов).', 'excel_path': None, **info}

    date_str = _dt.now().strftime('%d.%m.%Y %H:%M')
    base_name = original_name or 'таблица'
    title = f'Шаблон: {base_name}'

    total_cols = sum(len(df.columns) for df in sheets.values())
    total_rows = sum(len(df) for df in sheets.values())

    # ── Строим Markdown-контент по каждому листу ──────────────────────────────
    sheets_md = []

    for sheet_name, df in sheets.items():
        # Собираем метаданные колонок: (name, dtype, examples)
        col_meta = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            examples = df[col].dropna().head(3).tolist()
            col_meta.append((str(col), dtype, examples))

        # Генерируем целевые промпты через LLM
        col_prompts = _generate_column_prompts(sheet_name, col_meta, llm)

        # Таблица промптов
        tbl = '| Колонка | Тип | Пример | Целевой промпт |\n'
        tbl += '|---------|-----|--------|----------------|\n'
        for cname, ctype, cexamples in col_meta:
            ex_str = str(cexamples[0])[:40] if cexamples else '—'
            prompt_str = col_prompts.get(cname, '').replace('|', '\\|').replace('\n', ' ')
            tbl += f'| {cname} | {ctype} | {ex_str} | {prompt_str} |\n'

        sheets_md.append(
            f'## Лист: {sheet_name}\n\n'
            f'**Размер:** {len(df)} строк × {len(df.columns)} колонок\n\n'
            f'{tbl}'
        )

    comment_line = ''
    if caption and not any(kw in caption.lower() for kw in _TEMPLATE_KEYWORDS):
        comment_line = f'\n**Комментарий:** {caption}\n'

    sep = '\n\n---\n\n'
    sheets_block = sep.join(sheets_md)
    content = (
        f'# {title}\n\n'
        f'**Источник:** `{base_name}`\n'
        f'**Сохранён:** {date_str}\n'
        f'**Листов:** {len(sheets)} | **Всего колонок:** {total_cols} | **Строк:** {total_rows}\n'
        f'{comment_line}\n'
        f'---\n\n'
        f'{sheets_block}\n\n'
        f'---\n\n'
        f'## Как использовать шаблон\n\n'
        f'Чтобы заполнить шаблон данными из БД, отправь агенту:\n'
        f'- `/db заполни шаблон {base_name}`\n'
        f'- `/db по шаблону {base_name}: выгрузи данные из БД`\n\n'
        f'Агент вернёт Excel-файл в том же формате, что и входящий файл.'
    )

    # ── Сохраняем в wiki ──────────────────────────────────────────────────────
    try:
        from wiki_kb.models import WikiArticle, index_wiki_article
        from recordings.models import Space
        from django.utils.text import slugify

        PARENT_SLUG = 'chemico-query-templates'
        space = Space.objects.first()

        parent, _ = WikiArticle.objects.get_or_create(
            slug=PARENT_SLUG,
            defaults=dict(
                title='Chemico: таблицы-шаблоны для запросов',
                content=(
                    '# Chemico: таблицы-шаблоны\n\n'
                    'Дочерние страницы — Excel-файлы, сохранённые как шаблоны для построения запросов к БД.\n\n'
                    'Чтобы сохранить новый шаблон, отправь Excel-файл с подписью: **задай шаблон**.'
                ),
                space=space,
            )
        )

        safe_name = slugify(base_name, allow_unicode=False)[:40].strip('-') or 'file'
        slug = f'chemico-tpl-{safe_name}-{_uuid.uuid4().hex[:6]}'

        article = WikiArticle.objects.create(
            slug=slug,
            title=title,
            content=content,
            parent=parent,
            space=space,
        )
        index_wiki_article(article)

        sheet_summary = ', '.join(f'«{s}» ({len(df.columns)} кол.)' for s, df in sheets.items())

        return {
            'text': (
                f'✅ Шаблон сохранён в базе знаний!\n\n'
                f'📄 {title}\n'
                f'📊 {len(sheets)} лист(а): {sheet_summary}\n'
                f'🔗 /kb/{slug}/\n\n'
                f'Для каждого листа сгенерированы целевые промпты по колонкам.\n'
                f'Теперь можешь сказать агенту:\n'
                f'/db заполни шаблон {base_name}'
            ),
            'excel_path': None,
            'wiki_slug': slug,
            **info,
        }

    except Exception as e:
        logger.exception('save_excel_as_template wiki error')
        return {
            'text': f'Файл прочитан ({len(sheets)} листов, {total_cols} кол.), но сохранить в wiki не удалось: {e}',
            'excel_path': None,
            **info,
        }


def _df_to_markdown(df, max_rows: int = 20) -> str:
    """Конвертировать DataFrame в Markdown-таблицу для передачи в LLM."""
    import pandas as pd
    if len(df) > max_rows:
        sample = df.head(max_rows)
        note = f'\n_(показаны первые {max_rows} из {len(df)} строк)_'
    else:
        sample = df
        note = ''
    return sample.to_markdown(index=False) + note


def ask_about_excel(file_path: str, question: str) -> dict:
    """
    Ответить на вопрос по содержимому Excel-файла.
    Файл передаётся как контекст в промпт LLM.
    """
    import pandas as pd
    from chemico_agent.llm import get_langchain_llm, get_provider_info

    info = get_provider_info()

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        return {'text': f'Не удалось прочитать Excel: {e}', 'excel_path': None, **info}

    shape_info = f'Файл: {len(df)} строк, {len(df.columns)} колонок. Колонки: {list(df.columns)}'
    table_md   = _df_to_markdown(df)

    prompt = f"""Пользователь прислал Excel-файл и задаёт вопрос.

{shape_info}

Данные:
{table_md}

Вопрос: {question}

Ответь на русском языке, точно и кратко."""

    llm = get_langchain_llm(temperature=0.0)
    try:
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=prompt)])
        return {'text': response.content, 'excel_path': None, **info}
    except Exception as e:
        logger.exception('ask_about_excel error')
        return {'text': f'Ошибка LLM: {e}', 'excel_path': None, **info}


def enrich_excel_from_db(file_path: str, instruction: str) -> dict:
    """
    Дополнить Excel-файл данными из chemico_db.

    Агент анализирует колонки файла, строит SQL для поиска совпадений
    в БД (по ИНН, имени, id и т.д.) и добавляет новые колонки.
    """
    import pandas as pd
    from sqlalchemy import text, create_engine
    from chemico_agent.agent import CHEMICO_DB_URL, _get_db
    from chemico_agent.llm import get_langchain_llm, get_provider_info
    from chemico_agent.knowledge import get_wiki_context

    info = get_provider_info()

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        return {'text': f'Не удалось прочитать Excel: {e}', 'excel_path': None, **info}

    shape_info  = f'{len(df)} строк, {len(df.columns)} колонок'
    columns     = list(df.columns)
    sample_json = df.head(5).to_json(orient='records', force_ascii=False, indent=2)
    wiki_ctx    = get_wiki_context()

    # Шаг 1: LLM строит план обогащения (какой SQL выполнить для каждой строки)
    llm = get_langchain_llm(temperature=0.0)

    plan_prompt = f"""Пользователь прислал Excel ({shape_info}) и просит: "{instruction}"

Колонки файла: {columns}
Примеры данных (первые 5 строк):
{sample_json}

{wiki_ctx}

Задача: напиши один SQL-запрос который получит нужные данные из БД для ВСЕХ строк файла сразу.
Используй IN или JOIN чтобы получить данные для всех значений из Excel одним запросом.
Верни ТОЛЬКО SQL-запрос, без объяснений. Запрос должен начинаться с SELECT."""

    try:
        from langchain_core.messages import HumanMessage
        plan_response = llm.invoke([HumanMessage(content=plan_prompt)])
        sql = plan_response.content.strip()
        # Очищаем markdown-блоки если есть
        if '```' in sql:
            sql = sql.split('```')[1]
            if sql.startswith('sql'):
                sql = sql[3:]
        sql = sql.strip()
    except Exception as e:
        return {'text': f'Ошибка при генерации SQL: {e}', 'excel_path': None, **info}

    logger.info('enrich_excel SQL: %s', sql[:200])

    # Шаг 2: Выполняем SQL и получаем данные из БД
    try:
        engine = create_engine(CHEMICO_DB_URL)
        with engine.connect() as conn:
            db_df = pd.read_sql(text(sql), conn)
    except Exception as e:
        return {
            'text': f'Ошибка при выполнении SQL:\n`{sql[:300]}`\n\nОшибка: {e}',
            'excel_path': None,
            **info,
        }

    if db_df.empty:
        return {
            'text': 'SQL выполнен, но данные из БД не найдены. Попробуйте уточнить запрос.',
            'excel_path': None,
            **info,
        }

    # Шаг 3: LLM определяет как объединить df и db_df
    merge_prompt = f"""Есть два датафрейма:
1. Excel-файл: колонки {list(df.columns)}
2. Данные из БД: колонки {list(db_df.columns)}

Инструкция пользователя: "{instruction}"

Напиши Python-код (только тело, без функций/импортов) который объединяет их в переменную `result_df`.
Используй `df` и `db_df` как входные переменные. Код должен быть safe и простым.
Пример: result_df = df.merge(db_df, left_on='ИНН', right_on='inn', how='left')"""

    try:
        merge_response = llm.invoke([HumanMessage(content=merge_prompt)])
        merge_code = merge_response.content.strip()
        if '```' in merge_code:
            merge_code = merge_code.split('```')[1]
            if merge_code.startswith('python'):
                merge_code = merge_code[6:]
        merge_code = merge_code.strip()
    except Exception as e:
        # Fallback: просто добавляем все колонки из db_df
        merge_code = None

    try:
        if merge_code:
            local_vars = {'df': df, 'db_df': db_df}
            exec(merge_code, {'pd': pd}, local_vars)  # noqa: S102
            result_df = local_vars.get('result_df', pd.concat([df, db_df], axis=1))
        else:
            result_df = pd.concat([df, db_df], axis=1)
    except Exception as e:
        logger.warning('merge code failed (%s), using concat', e)
        result_df = pd.concat([df, db_df], axis=1)

    # Шаг 4: Сохраняем результат
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(tempfile.gettempdir(), f'chemico_enriched_{ts}.xlsx')

    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        result_df.to_excel(writer, index=False, sheet_name='Данные')
        ws = writer.sheets['Данные']
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

    added_cols = [c for c in result_df.columns if c not in df.columns]
    return {
        'text': (
            f'✅ Файл дополнен.\n'
            f'Добавлено колонок: {len(added_cols)} ({", ".join(str(c) for c in added_cols[:5])}{"..." if len(added_cols) > 5 else ""})\n'
            f'Строк: {len(result_df)}'
        ),
        'excel_path': out_path,
        **info,
    }
