"""
Chemico DB Agent — Telegram-хендлер.

Команды бота:
  /db <вопрос>       — вопрос по базе данных
  /db_model          — текущий LLM-провайдер
  /db_mode [0|1|2]   — переключить режим базы знаний
  /db_help           — справка

Режимы базы знаний (SystemConfig.chemico_kb_mode):
  0 — только БД, без Wiki (сырой агент)
  1 — БД + Wiki-статья chemico-db-schema
  2 — БД + Wiki chemico + дополнительный Wiki-раздел

Входящие Excel-файлы:
  Пришли .xlsx с подписью → агент ответит на вопрос по файлу
  Пришли .xlsx с "дополни/обогати/подтяни" → агент дополнит файл данными из БД

Подключение: handle_chemico_command() и handle_chemico_document() вызываются из tg-webhook.
"""
from __future__ import annotations

import logging
import os
import tempfile

import requests as req
from django.conf import settings

logger = logging.getLogger(__name__)

_MODE_LABELS = {
    '0': '🔴 Без Wiki (только БД)',
    '1': '🟡 Wiki Chemico (схема)',
    '2': '🟢 Wiki Chemico + доп. раздел',
}


def _send(chat_id: int, text: str, token: str | None = None) -> None:
    """Отправить сообщение без Markdown-парсинга (URL с _ ломают Telegram Markdown)."""
    token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        req.post(url, json={'chat_id': chat_id, 'text': text}, timeout=10)
    except Exception as e:
        logger.error('_send error: %s', e)


def _send_document(chat_id: int, file_path: str, caption: str = '', token: str | None = None) -> None:
    token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return
    url = f'https://api.telegram.org/bot{token}/sendDocument'
    try:
        with open(file_path, 'rb') as f:
            req.post(url, data={'chat_id': chat_id, 'caption': caption}, files={'document': f}, timeout=30)
    except Exception as e:
        logger.error('_send_document error: %s', e)


def _download_tg_file(file_id: str, token: str) -> str | None:
    """Скачать файл из Telegram, вернуть путь к временному файлу."""
    url = f'https://api.telegram.org/bot{token}/getFile'
    try:
        r = req.post(url, json={'file_id': file_id}, timeout=10)
        data = r.json()
        if not data.get('ok'):
            return None
        file_path = data['result']['file_path']
        file_url = f'https://api.telegram.org/file/bot{token}/{file_path}'
        r2 = req.get(file_url, timeout=60, stream=True)
        ext = os.path.splitext(file_path)[1] or '.xlsx'
        fd, local_path = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, 'wb') as f:
            for chunk in r2.iter_content(chunk_size=65536):
                f.write(chunk)
        return local_path
    except Exception as e:
        logger.error('_download_tg_file error: %s', e)
        return None


def _send_result(chat_id: int, result: dict, token: str | None = None) -> None:
    """Отправить ответ агента + Excel-файл если есть."""
    answer   = result.get('text', 'Нет ответа')
    provider = result.get('provider', '?')
    model    = result.get('model', '?')
    footer   = f'\n\n_({provider} / {model})_'

    if len(answer) + len(footer) > 4090:
        answer = answer[:4050] + '...'
    _send(chat_id, answer + footer, token)

    excel_path = result.get('excel_path')
    excel_url  = result.get('excel_url')
    if excel_path and os.path.exists(str(excel_path)):
        caption = f'📊 Данные из Chemico DB'
        if excel_url:
            caption += f'\n🔗 {excel_url}'
        _send_document(chat_id, str(excel_path), caption=caption, token=token)


def handle_chemico_document(chat_id: int, message: dict, token: str | None = None) -> bool:
    """
    Обработать входящий документ (.xlsx / .xls).

    Returns True если файл обработан нами.
    """
    doc = message.get('document', {})
    mime = doc.get('mime_type', '')
    fname = doc.get('file_name', '')

    is_excel = (
        mime in (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-excel',
        )
        or fname.lower().endswith(('.xlsx', '.xls'))
    )
    if not is_excel:
        return False

    caption = (message.get('caption') or '').strip()
    file_id = doc.get('file_id')
    if not file_id:
        return False

    bot_token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    _send(chat_id, '📥 Получил Excel-файл, скачиваю...', token)

    local_path = _download_tg_file(file_id, bot_token)
    if not local_path:
        _send(chat_id, '❌ Не удалось скачать файл.', token)
        return True

    try:
        from chemico_agent.excel_input import (
            ask_about_excel, enrich_excel_from_db,
            save_excel_as_template,
            _is_enrich_request, _is_template_request,
        )

        fname = doc.get('file_name', 'файл.xlsx')

        if _is_template_request(caption):
            _send(chat_id, '📋 Сохраняю шаблон в базу знаний...', token)
            result = save_excel_as_template(local_path, original_name=fname, caption=caption, chat_id=chat_id)
        elif _is_enrich_request(caption):
            instruction = caption or 'Дополни файл данными из базы данных'
            _send(chat_id, '⏳ Дополняю файл данными из Chemico БД...', token)
            result = enrich_excel_from_db(local_path, instruction)
        else:
            question = caption or 'Опиши содержимое файла: сколько строк, какие колонки, ключевые показатели'
            _send(chat_id, '⏳ Анализирую файл...', token)
            result = ask_about_excel(local_path, question)

        _send_result(chat_id, result, token)

        # Создать Excel Studio сессию (кроме ask_about_excel без файла)
        _create_excel_session(chat_id, fname, local_path, result, caption, token)

    except Exception as e:
        logger.exception('chemico handle_excel error')
        _send(chat_id, f'❌ Ошибка обработки файла: {e}', token)
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    return True


def _create_excel_session(chat_id, fname, orig_path, result, instruction, token):
    """Создать ExcelSession и прислать ссылку пользователю."""
    try:
        import pandas as pd, math as _math
        from recordings.models import ExcelSession, SiteUser
        from recordings.telegram_service import _site_url

        # Определяем финальный файл: enriched или оригинальный
        file_path = result.get('excel_path') or orig_path
        if not file_path or not os.path.exists(str(file_path)):
            file_path = orig_path

        df = pd.read_excel(file_path, engine='openpyxl')
        columns = [str(c) for c in df.columns]
        rows = []
        for _, row in df.iterrows():
            r = {}
            for k, v in row.items():
                if v is None or (isinstance(v, float) and _math.isnan(v)):
                    r[str(k)] = None
                elif hasattr(v, 'isoformat'):
                    r[str(k)] = str(v)
                else:
                    r[str(k)] = v
            rows.append(r)

        # Конфиг: если файл был обогащён — помечаем новые столбцы
        orig_df = pd.read_excel(orig_path, engine='openpyxl') if file_path != orig_path else None
        orig_cols = set(str(c) for c in orig_df.columns) if orig_df is not None else set(columns)
        col_configs = {}
        for col in columns:
            if col not in orig_cols and instruction:
                col_configs[col] = {
                    'prompt': instruction,
                    'key_col': '',
                    'status': 'filled',
                    'source': 'telegram_enrich',
                }

        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        session = ExcelSession.objects.create(
            user=user,
            space=user.space if user else None,
            filename=fname,
            columns=columns,
            rows=rows[:2000],  # cap at 2000 rows
            col_configs=col_configs,
            tg_chat_id=chat_id,
            created_via='telegram',
        )
        site_url = _site_url()
        link = f'{site_url}/db-excel/s/{session.id}/'
        _send(chat_id,
              f'📊 Открыть в Excel Studio:\n{link}\n\n'
              f'Там можно настроить промпт для каждого столбца и перезаполнить данные из БД.',
              token)
    except Exception as e:
        logger.warning('_create_excel_session failed: %s', e)


def handle_chemico_command(chat_id: int, text: str, token: str | None = None) -> bool:
    """
    Обработать команду /db* из Telegram.

    Returns True если команда обработана.
    """
    from recordings.models import SystemConfig

    text = (text or '').strip()
    parts = text.split(None, 1)
    command = parts[0].lower() if parts else ''

    # /db_model — показать текущий провайдер
    if command == '/db_model':
        from chemico_agent.llm import get_provider_info
        info = get_provider_info()
        mode = SystemConfig.get('chemico_kb_mode', '1')
        _send(chat_id, (
            f'🤖 *Провайдер:* {info["provider"]}\n'
            f'📦 *Модель:* `{info["model"]}`\n'
            f'📚 *Режим знаний:* {_MODE_LABELS.get(mode, mode)}'
        ), token)
        return True

    # /db_mode [0|1|2] — переключить режим базы знаний
    if command == '/db_mode':
        mode_arg = parts[1].strip() if len(parts) > 1 else ''
        if mode_arg in ('0', '1', '2'):
            SystemConfig.set('chemico_kb_mode', mode_arg, description='Chemico KB mode: 0=none,1=wiki,2=wiki+extra')
            label = _MODE_LABELS[mode_arg]
            _send(chat_id, f'✅ Режим базы знаний: {label}', token)
        else:
            current = SystemConfig.get('chemico_kb_mode', '1')
            lines = ['*Режимы базы знаний:*']
            for k, v in _MODE_LABELS.items():
                mark = ' ← текущий' if k == current else ''
                lines.append(f'`/db_mode {k}` — {v}{mark}')
            _send(chat_id, '\n'.join(lines), token)
        return True

    # /db_clear — очистить историю диалога
    if command == '/db_clear':
        try:
            from recordings.models import BotChatHistory
            deleted, _ = BotChatHistory.objects.filter(chat_id=chat_id, bot_id=None).delete()
            _send(chat_id, f'🗑 История диалога очищена ({deleted} сообщений).', token)
        except Exception as e:
            _send(chat_id, f'❌ Ошибка: {e}', token)
        return True

    # /db_help или /db без аргументов
    if command in ('/db_help', '/db') and len(parts) == 1:
        mode = SystemConfig.get('chemico_kb_mode', '1')
        _send(chat_id, (
            '📊 *Chemico DB Агент*\n\n'
            '*Вопросы по БД:*\n'
            '`/db Топ-10 сделок за последний месяц`\n'
            '`/db Сколько активных бизнес-процессов?`\n'
            '`/db Выгрузи всех клиентов в Excel`\n\n'
            '*Работа с Excel-файлами:*\n'
            'Пришли .xlsx файл с вопросом → получи ответ по файлу\n'
            'Пришли .xlsx с "дополни" → файл обогатится данными из БД\n\n'
            f'*Режим знаний:* {_MODE_LABELS.get(mode, mode)}\n'
            '`/db_mode` — сменить режим\n'
            '`/db_model` — текущий провайдер\n'
            '`/db_clear` — очистить историю диалога'
        ), token)
        return True

    # /db <вопрос>
    if not text.lower().startswith('/db '):
        return False

    question = text[4:].strip()
    if not question:
        _send(chat_id, 'Укажите вопрос после /db', token)
        return True

    _send(chat_id, '⏳ Анализирую данные...', token)

    try:
        from chemico_agent.agent import ask
        result = ask(question, chat_id=chat_id)
    except Exception as e:
        logger.exception('chemico ask error')
        _send(chat_id, f'❌ Ошибка: {e}', token)
        return True

    _send_result(chat_id, result, token)
    return True
