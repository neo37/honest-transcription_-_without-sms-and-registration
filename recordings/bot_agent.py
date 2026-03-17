"""
ReAct AI-агент для Telegram-ботов.

Цикл:
  user msg → [history + system_prompt] → LLM
  LLM отвечает:
    TOOL: {"name": "...", "args": {...}}  → выполняем инструмент → добавляем результат → снова к LLM
    ANSWER: <текст>                       → отправляем пользователю
  Максимум MAX_ITERATIONS шагов.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5
HISTORY_WINDOW = 16   # сообщений в контексте

# Порог score: ниже — предупреждаем LLM о слабом совпадении
LOW_SCORE_THRESHOLD = 0.45
# Ниже этого — сразу просим уточнить (без вызова LLM)
CLARIFY_THRESHOLD = 0.38
# Минимальная длина запроса для авто-уточнения (в словах)
CLARIFY_MIN_WORDS = 3


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(custom_bot=None, article_ids=None, owner=None) -> str:
    # If custom bot has a custom prompt — use it as-is (prepend owner email hint)
    if custom_bot and custom_bot.system_prompt and custom_bot.system_prompt.strip():
        owner_hint = ''
        if owner:
            owner_hint = f'\n\nВладелец бота: {owner.email}'
        return custom_bot.system_prompt.strip() + owner_hint

    section = ''
    if custom_bot and custom_bot.root_article:
        section = f'\nТы специализируешься на разделе «{custom_bot.root_article.title}».'

    booking_url = 'https://baza.business-pad.com/meetings/book/400df63f-8e53-45cf-99d7-37595ce31588/'

    meeting_tools = ''
    if owner:
        meeting_tools = f"""
- **create_meeting** — создать встречу (только для владельца бота, только по явной просьбе)
  args: {{"title": "название", "scheduled_at": "2024-03-15T14:00"}}

- **get_free_slots** — свободные слоты на дату (только по явному запросу пользователя)
  args: {{"date": "2024-03-15"}}

Ссылка для самостоятельного бронирования встречи: {booking_url}
(Отправляй эту ссылку пользователю, если он хочет записаться на встречу самостоятельно)
"""

    owner_info = ''
    if owner:
        owner_info = f'\nВладелец бота: {owner.email}'

    wiki_tools = """
- **save_to_wiki** — сохранить информацию как персональную статью базы знаний
  args: {"title": "Заголовок", "content": "Содержимое в Markdown", "parent_slug": "опционально"}
  Используй для запоминания фактов о клиентах, договорённостей, заметок.

- **update_wiki_article** — дополнить или обновить существующую статью
  args: {"slug": "article-slug", "content": "текст", "append": true/false}

- **list_personal_articles** — показать что уже сохранено в базе знаний бота
  args: {}
"""

    return f"""Ты AI-агент компании с доступом к корпоративной базе знаний.{section}{owner_info}

Для получения информации используй инструменты. Отвечай только после сбора достаточных данных.
Отвечай на русском языке, кратко и по делу.

## Доступные инструменты

- **search_wiki** — семантический поиск по базе знаний
  args: {{"query": "текст запроса"}}

- **get_article** — получить полное содержимое статьи по slug
  args: {{"slug": "article-slug"}}

- **list_articles** — список статей в текущем разделе (без содержимого)
  args: {{}}

- **list_meetings** — активные видеовстречи прямо сейчас
  args: {{}}
{meeting_tools}{wiki_tools}
## Правила работы со встречами

- Встречи создаются ТОЛЬКО для владельца бота — никогда для других пользователей.
- Инструменты встреч используй ТОЛЬКО по явной просьбе пользователя. Не предлагай встречи сам.
- При запросе слотов: показывай свободные окна, занятые — только как «занято» без деталей.
- При создании встречи уточни название и точное время (дата + час + минуты).
- Если пользователь (не владелец) хочет записаться / забронировать время — давай ссылку: {booking_url}

## Формат ответа

Когда нужен инструмент — отвечай СТРОГО так (ничего лишнего):
TOOL: {{"name": "имя", "args": {{...}}}}

Когда готов дать финальный ответ пользователю:
ANSWER: <твой ответ>

Важно: если ответ основан на конкретных статьях базы знаний — в конце ответа добавь строку:
SOURCES: slug1,slug2

Не придумывай информацию. Если данных нет — скажи об этом.
Если вопрос слишком расплывчатый или ничего не найдено — попроси пользователя уточнить."""


# ── Tools ─────────────────────────────────────────────────────────────────────

def _tool_search_wiki(args: dict, space, article_ids=None, _source_tracker=None, owner=None, **__) -> str:
    from wiki_kb.models import wiki_semantic_search
    query = args.get('query', '')
    if not query:
        return 'Ошибка: не указан запрос.'
    results = wiki_semantic_search(query, space=space, article_ids=article_ids, top_k=4, owner=owner)
    if not results:
        return 'Ничего не найдено.'

    max_score = max(r['score'] for r in results)
    lines = []
    for r in results:
        slug = r['article'].slug
        if _source_tracker is not None:
            _source_tracker[slug] = r['article']
        lines.append(f'[{slug}] {r["article"].title} (score={r["score"]:.2f})\n{r["excerpt"][:200]}')

    result = '\n\n'.join(lines)

    if max_score < LOW_SCORE_THRESHOLD:
        result = (
            f'⚠️ Низкое совпадение (max score={max_score:.2f}). '
            f'Результаты могут быть нерелевантны. Если данных недостаточно — попроси пользователя уточнить.\n\n'
            + result
        )
    return result


def _tool_get_article(args: dict, space, article_ids=None, _source_tracker=None, owner=None, **__) -> str:
    from wiki_kb.models import WikiArticle
    from django.db.models import Q
    slug = args.get('slug', '')
    if not slug:
        return 'Ошибка: не указан slug.'
    qs = WikiArticle.objects.filter(slug=slug, is_deleted=False)
    if space:
        qs = qs.filter(space=space)
    if owner:
        qs = qs.filter(Q(is_personal=False) | Q(is_personal=True, created_by=owner))
    else:
        qs = qs.filter(is_personal=False)
    art = qs.first()
    if not art:
        return f'Статья «{slug}» не найдена.'
    if _source_tracker is not None:
        _source_tracker[slug] = art
    content = art.content[:3000]
    if len(art.content) > 3000:
        content += '\n\n...[обрезано]'
    return f'# {art.title}\n\n{content}'


def _tool_list_articles(args: dict, space, article_ids=None, owner=None, **__) -> str:
    from wiki_kb.models import WikiArticle
    from django.db.models import Q
    qs = WikiArticle.objects.filter(is_deleted=False).order_by('order', 'title')
    if space:
        qs = qs.filter(space=space)
    if article_ids:
        qs = qs.filter(pk__in=article_ids)
    if owner:
        qs = qs.filter(Q(is_personal=False) | Q(is_personal=True, created_by=owner))
    else:
        qs = qs.filter(is_personal=False)
    arts = qs[:30]
    if not arts:
        return 'Статей не найдено.'
    return '\n'.join(f'- [{a.slug}] {a.title}' for a in arts)


def _tool_create_meeting(args: dict, owner=None, **__) -> str:
    """Создать встречу для владельца бота."""
    import uuid as _uuid
    from django.utils.text import slugify
    from django.utils import timezone
    import datetime

    if not owner:
        return 'Ошибка: не определён владелец встречи.'

    title = args.get('title', '').strip()
    scheduled_at_str = args.get('scheduled_at', '').strip()

    if not title:
        return 'Укажите название встречи.'
    if not scheduled_at_str:
        return 'Укажите дату и время встречи в формате YYYY-MM-DDTHH:MM, например 2024-03-15T14:00.'

    try:
        dt = datetime.datetime.fromisoformat(scheduled_at_str)
        if dt.tzinfo is None:
            dt = timezone.make_aware(dt)
    except (ValueError, TypeError):
        return f'Неверный формат даты «{scheduled_at_str}». Используйте YYYY-MM-DDTHH:MM.'

    from recordings.models import MeetingRoom, MeetingAttendee
    base = slugify(title)[:40] or 'meeting'
    room_name = base
    if MeetingRoom.objects.filter(room_name=room_name).exists():
        room_name = f'{base}-{_uuid.uuid4().hex[:6]}'

    join_url = f'https://meet.business-pad.com/rooms/{room_name}'
    meeting = MeetingRoom.objects.create(
        room_name=room_name,
        title=title,
        scheduled_at=dt,
        created_by=owner,
        space=owner.space,
        join_url=join_url,
    )
    MeetingAttendee.objects.get_or_create(user=owner, meeting=meeting)

    import zoneinfo as _zi
    dt_display = dt.astimezone(_zi.ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M') + ' МСК'
    return (
        f'✅ Встреча создана!\n'
        f'📌 {title}\n'
        f'🕐 {dt_display}\n'
        f'🔗 {join_url}'
    )


def _tool_get_free_slots(args: dict, owner=None, **__) -> str:
    """Свободные слоты на дату. Занятые — только «занято», без деталей."""
    import datetime
    from django.utils import timezone

    if not owner:
        return 'Ошибка: не определён пользователь.'

    date_str = args.get('date', '')
    try:
        if date_str:
            d = datetime.date.fromisoformat(date_str)
        else:
            d = timezone.localdate()
    except (ValueError, TypeError):
        return f'Неверный формат даты «{date_str}». Используйте YYYY-MM-DD.'

    from recordings.models import MeetingRoom, RecurringBusyTime

    day_start = datetime.time(9, 0)
    day_end = datetime.time(18, 0)
    slot_min = 30

    slots = []
    cur = datetime.datetime.combine(d, day_start)
    end_dt = datetime.datetime.combine(d, day_end)
    while cur + datetime.timedelta(minutes=slot_min) <= end_dt:
        slots.append((cur.time(), (cur + datetime.timedelta(minutes=slot_min)).time()))
        cur += datetime.timedelta(minutes=slot_min)

    busy_intervals = []

    # Повторяющиеся блоки
    weekday = d.weekday()
    for r in RecurringBusyTime.objects.filter(owner=owner, is_active=True):
        if r.repeat == 'daily' or (r.repeat == 'weekdays' and weekday < 5):
            busy_intervals.append((r.start_time, r.end_time))

    # Запланированные встречи на этот день — только «занято»
    tz = timezone.get_current_timezone()
    d_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min))
    d_end = timezone.make_aware(datetime.datetime.combine(d, datetime.time.max))
    for m in MeetingRoom.objects.filter(
        created_by=owner,
        scheduled_at__range=(d_start, d_end),
        ended_at__isnull=True,
    ):
        local_start = m.scheduled_at.astimezone(tz).time()
        local_end = (m.scheduled_at.astimezone(tz) + datetime.timedelta(hours=1)).time()
        busy_intervals.append((local_start, local_end))

    def overlaps(s, e):
        return any(s < b_end and e > b_start for b_start, b_end in busy_intervals)

    fmt = lambda s, e: f'{s.strftime("%H:%M")}–{e.strftime("%H:%M")}'
    free = [fmt(s, e) for s, e in slots if not overlaps(s, e)]
    busy = [fmt(s, e) for s, e in slots if overlaps(s, e)]

    day_names = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    day_display = f'{d.strftime("%d.%m.%Y")} ({day_names[d.weekday()]})'

    lines = [f'📅 {day_display}']
    if free:
        lines.append(f'✅ Свободно: {", ".join(free)}')
    else:
        lines.append('✅ Свободных слотов нет')
    if busy:
        lines.append(f'🔴 Занято: {", ".join(busy)}')

    return '\n'.join(lines)


def _tool_list_meetings(args: dict, space, **__) -> str:
    """Активные встречи через API сайта."""
    import requests as req
    from django.conf import settings as _s
    from recordings.telegram_service import _site_url

    site_url = _site_url()
    master_key = getattr(_s, 'MASTER_API_KEY', '')

    if not master_key:
        return 'Сервис встреч недоступен.'

    try:
        params = {}
        if space:
            params['space_slug'] = space.slug
        resp = req.get(
            f'{site_url}/api/active-meetings/',
            params=params,
            headers={'X-Agent-Key': master_key},
            timeout=10,
        )
        resp.raise_for_status()
        rooms = resp.json().get('meetings', [])
    except Exception as e:
        logger.warning('list_meetings API failed: %s', e)
        return 'Не удалось получить список встреч.'

    if not rooms:
        return 'Активных встреч нет.'

    return '\n'.join(
        f'- {rm["title"]} ({rm["participants"]} уч.) → {rm["url"]}'
        for rm in rooms
    )


def _get_or_create_bot_kb_parent(owner, space, custom_bot):
    """Получить или создать корневой раздел личной базы знаний бота."""
    from wiki_kb.models import WikiArticle
    from django.utils.text import slugify as _slugify
    import uuid as _uuid

    bot_name = (custom_bot.name or custom_bot.username or 'bot') if custom_bot else 'personal'
    slug_base = _slugify(f'kb-bot-{bot_name}', allow_unicode=False) or 'kb-bot'
    # Ищем существующий раздел владельца для этого бота
    existing = WikiArticle.objects.filter(
        space=space, created_by=owner, is_personal=True, is_deleted=False,
        slug__startswith=slug_base,
        parent__isnull=True,
    ).first()
    if existing:
        return existing

    slug = slug_base + '-' + _uuid.uuid4().hex[:6]
    bot_label = custom_bot.name or custom_bot.username if custom_bot else 'личная'
    parent = WikiArticle.objects.create(
        title=f'База знаний: {bot_label}',
        slug=slug,
        content=f'Личная база знаний бота «{bot_label}». Видна только владельцу и его ботам.',
        space=space,
        created_by=owner,
        is_personal=True,
    )
    _index_wiki_article(parent)
    return parent


def _index_wiki_article(article):
    """Обновить эмбеддинг статьи вики."""
    try:
        from wiki_kb.models import WikiArticleEmbedding
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device='cpu')
        text = f'{article.title}\n\n{article.content}'[:4096]
        emb = model.encode(text, convert_to_numpy=True)
        WikiArticleEmbedding.objects.update_or_create(
            article=article, defaults={'embedding': emb.tolist()}
        )
    except Exception as e:
        logger.warning('wiki index failed for %s: %s', article.slug, e)


def _tool_save_to_wiki(args: dict, owner=None, space=None, custom_bot=None, **__) -> str:
    """Сохранить информацию как персональную статью вики (база знаний бота)."""
    from wiki_kb.models import WikiArticle
    from django.utils.text import slugify as _slugify
    import uuid as _uuid

    if not owner or not space:
        return 'Ошибка: не определён владелец или пространство.'

    title = (args.get('title') or '').strip()
    content = (args.get('content') or '').strip()
    parent_slug = (args.get('parent_slug') or '').strip()

    if not title:
        return 'Укажите заголовок статьи (title).'
    if not content:
        return 'Укажите содержимое статьи (content).'

    # Родительский раздел
    if parent_slug:
        parent = WikiArticle.objects.filter(slug=parent_slug, space=space, is_deleted=False).first()
    else:
        parent = _get_or_create_bot_kb_parent(owner, space, custom_bot)

    # Проверить: существует ли уже статья с таким заголовком в этом разделе
    existing = WikiArticle.objects.filter(
        title=title, space=space, parent=parent, is_deleted=False,
    ).first()

    if existing:
        existing.content = content
        existing.is_personal = True
        if not existing.created_by:
            existing.created_by = owner
        existing.save(update_fields=['content', 'is_personal', 'created_by'])
        _index_wiki_article(existing)
        return f'✅ Статья «{title}» обновлена (slug: {existing.slug})'

    slug = _slugify(title, allow_unicode=False)[:50] or 'article'
    if WikiArticle.objects.filter(slug=slug).exists():
        slug = slug + '-' + _uuid.uuid4().hex[:6]

    article = WikiArticle.objects.create(
        title=title,
        slug=slug,
        content=content,
        space=space,
        parent=parent,
        created_by=owner,
        is_personal=True,
    )
    _index_wiki_article(article)
    return f'✅ Статья «{title}» создана в разделе «{parent.title}» (slug: {article.slug})'


def _tool_update_wiki_article(args: dict, owner=None, space=None, **__) -> str:
    """Обновить содержимое существующей персональной статьи вики по slug."""
    from wiki_kb.models import WikiArticle

    if not owner:
        return 'Ошибка: не определён владелец.'

    slug = (args.get('slug') or '').strip()
    content = (args.get('content') or '').strip()
    append = args.get('append', False)

    if not slug:
        return 'Укажите slug статьи.'
    if not content:
        return 'Укажите новое содержимое (content).'

    article = WikiArticle.objects.filter(
        slug=slug, space=space, created_by=owner, is_deleted=False,
    ).first()
    if not article:
        return f'Статья «{slug}» не найдена или недоступна.'

    if append:
        article.content = article.content.rstrip() + '\n\n' + content
    else:
        article.content = content

    article.save(update_fields=['content'])
    _index_wiki_article(article)
    return f'✅ Статья «{article.title}» обновлена.'


def _tool_list_personal_articles(args: dict, owner=None, space=None, **__) -> str:
    """Список персональных статей базы знаний бота."""
    from wiki_kb.models import WikiArticle

    if not owner:
        return 'Ошибка: не определён владелец.'

    qs = WikiArticle.objects.filter(
        space=space, created_by=owner, is_personal=True, is_deleted=False,
    ).order_by('parent_id', 'order', 'title')[:50]

    if not qs:
        return 'Персональных статей пока нет.'

    return '\n'.join(f'- [{a.slug}] {a.title}' + (f' (в: {a.parent.title})' if a.parent else '') for a in qs)


TOOLS = {
    'search_wiki': _tool_search_wiki,
    'get_article': _tool_get_article,
    'list_articles': _tool_list_articles,
    'list_meetings': _tool_list_meetings,
    'create_meeting': _tool_create_meeting,
    'get_free_slots': _tool_get_free_slots,
    'save_to_wiki': _tool_save_to_wiki,
    'update_wiki_article': _tool_update_wiki_article,
    'list_personal_articles': _tool_list_personal_articles,
}


# ── OpenAI tool definitions ───────────────────────────────────────────────────

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "Семантический поиск по корпоративной базе знаний",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Поисковый запрос"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_article",
            "description": "Получить полное содержимое статьи базы знаний по slug",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string", "description": "Slug статьи"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_articles",
            "description": "Список всех статей в текущем разделе базы знаний",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_meetings",
            "description": "Активные видеовстречи прямо сейчас",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_meeting",
            "description": "Создать встречу для владельца бота. Всегда создаёт только для владельца, никогда для других.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название встречи"},
                    "scheduled_at": {"type": "string", "description": "Дата и время в ISO 8601, например 2024-03-15T14:00"},
                },
                "required": ["title", "scheduled_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": "Получить свободные временные слоты на дату (9:00-18:00, шаг 30 мин). Занятые слоты показываются только как 'занято' без деталей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_to_wiki",
            "description": (
                "Сохранить информацию как персональную статью в базе знаний бота. "
                "Используй когда нужно запомнить что-то о клиенте, договорённость, факт или заметку. "
                "Статья будет видна только владельцу бота."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Заголовок статьи"},
                    "content": {"type": "string", "description": "Содержимое в формате Markdown"},
                    "parent_slug": {"type": "string", "description": "Slug родительской статьи (опционально; если не указан — сохраняется в корневой раздел базы знаний бота)"},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_wiki_article",
            "description": "Обновить или дополнить существующую персональную статью базы знаний бота.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Slug статьи"},
                    "content": {"type": "string", "description": "Новое содержимое или дополнение"},
                    "append": {"type": "boolean", "description": "True — добавить в конец, False — заменить полностью"},
                },
                "required": ["slug", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_personal_articles",
            "description": "Список персональных статей базы знаний бота (что уже сохранено).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ── LLM backends ──────────────────────────────────────────────────────────────

# Модели по умолчанию для каждого провайдера
_LLM_DEFAULT_MODELS = {
    'gonka':  'Qwen/Qwen3-235B-A22B-Instruct-2507-FP8',
    'openai': 'gpt-4o-mini',
    'grok':   'grok-3-mini-fast',
}

# Gonka node endpoints (в порядке предпочтения)
_GONKA_NODES = [
    'https://node3.gonka.ai',
    'http://node1.gonka.ai:8000',
    'http://node2.gonka.ai:8000',
]


def _get_llm_config() -> tuple[str, str, str]:
    """Возвращает (provider, credential, model).

    provider='gonka' → credential=private_key  (используется GonkaOpenAI SDK)
    provider='openai' → credential=api_key     (используется requests + Bearer)
    provider='grok'  → credential=api_key      (используется requests + Bearer)

    Явный выбор провайдера читается из SystemConfig.llm_provider.
    Если не задан — автовыбор: gonka (если есть ключ) > grok > openai.
    """
    from recordings.models import SystemConfig
    import os

    explicit = SystemConfig.get('llm_provider', '').strip().lower()

    # ── Gonka ──
    gonka_key = os.environ.get('GONKA_PRIVATE_KEY') or SystemConfig.get('gonka_private_key', '')
    if explicit == 'gonka' or (not explicit and gonka_key):
        model = SystemConfig.get('gonka_model', _LLM_DEFAULT_MODELS['gonka'])
        return 'gonka', gonka_key, model

    # ── Grok (xAI) ──
    grok_key = SystemConfig.get('grok_api_key', '')
    if explicit == 'grok' or (not explicit and grok_key):
        model = SystemConfig.get('grok_model', _LLM_DEFAULT_MODELS['grok'])
        return 'grok', grok_key, model

    # ── OpenAI ──
    openai_key = SystemConfig.get('openai_api_key', '')
    model = SystemConfig.get('openai_model', _LLM_DEFAULT_MODELS['openai'])
    return 'openai', openai_key, model


def _get_openai_key() -> str:
    """Совместимость: возвращает активный credential."""
    _, key, _ = _get_llm_config()
    return key


def _get_openai_model() -> str:
    """Совместимость: возвращает активную модель."""
    _, _, model = _get_llm_config()
    return model


def _call_openai(messages: list[dict], tools: list) -> dict:
    """Вызов LLM API с нативным tool calling (OpenAI-совместимый формат)."""
    import os
    provider, credential, model = _get_llm_config()
    logger.debug('LLM call: provider=%s model=%s', provider, model)

    if provider == 'gonka':
        # Используем официальный Gonka SDK (криптографическая подпись)
        from gonka_openai import GonkaOpenAI
        node_url = os.environ.get('GONKA_NODE_URL', _GONKA_NODES[0])
        gonka_address = os.environ.get('GONKA_ADDRESS', 'gonka12cf6d7346f2k6fe4hsl9nnehhzxnh0daw74nws')
        client = GonkaOpenAI(
            gonka_private_key=credential,
            source_url=node_url,
            gonka_address=gonka_address,
        )
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice='auto',
            temperature=0.3,
        )
        # Конвертируем объект в dict-совместимый формат
        return resp.model_dump()

    # OpenAI-compatible HTTP (openai / grok)
    import requests as req
    _urls = {
        'openai': 'https://api.openai.com/v1/chat/completions',
        'grok':   'https://api.x.ai/v1/chat/completions',
    }
    url = _urls.get(provider, _urls['openai'])
    resp = req.post(
        url,
        headers={'Authorization': f'Bearer {credential}', 'Content-Type': 'application/json'},
        json={
            'model': model,
            'messages': messages,
            'tools': tools,
            'tool_choice': 'auto',
            'temperature': 0.3,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _call_legacy_llm(messages: list[dict], session_id: str) -> str:
    """Fallback: вызов через AIConfig (ReAct через текстовый формат)."""
    from recordings.services import call_llm_api
    parts = []
    for m in messages:
        if m['role'] == 'system':
            parts.append(f'[SYSTEM]\n{m["content"]}')
        elif m['role'] == 'user':
            parts.append(f'[USER]\n{m["content"]}')
        elif m['role'] == 'assistant':
            parts.append(f'[ASSISTANT]\n{m["content"]}')
        elif m['role'] == 'tool':
            parts.append(f'[TOOL RESULT: {m.get("name", "")}]\n{m["content"]}')
    return call_llm_api(
        prompt='\n\n---\n\n'.join(parts),
        session_id=session_id,
        log_id=str(uuid.uuid4()),
        timeout=90,
    )


# ── Parse ReAct response (для fallback) ───────────────────────────────────────

_TOOL_RE = re.compile(r'TOOL:\s*(\{.*?\})', re.DOTALL)
_ANSWER_RE = re.compile(r'ANSWER:\s*(.*)', re.DOTALL)
_SOURCES_RE = re.compile(r'SOURCES:\s*([^\n]+)', re.IGNORECASE)


def _parse_react(text: str) -> tuple[str, dict | None]:
    m = _TOOL_RE.search(text)
    if m:
        try:
            return 'tool', json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return 'answer', None


def _extract_answer(text: str) -> str:
    m = _ANSWER_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _extract_sources_from_text(text: str) -> list[str]:
    """Извлечь SOURCES: slug1,slug2 из ответа LLM."""
    m = _SOURCES_RE.search(text)
    if not m:
        return []
    return [s.strip() for s in m.group(1).split(',') if s.strip()]


def _clean_answer(text: str) -> str:
    """Убрать строку SOURCES: из финального ответа пользователю."""
    return _SOURCES_RE.sub('', text).strip()


# ── Pre-search: проверка релевантности до вызова LLM ─────────────────────────

def _presearch(query: str, space, article_ids) -> float | None:
    """Быстрый поиск для оценки релевантности. Возвращает max score или None при ошибке."""
    try:
        from wiki_kb.models import wiki_semantic_search
        results = wiki_semantic_search(query, space=space, article_ids=article_ids, top_k=3)
        if not results:
            return 0.0
        return max(r['score'] for r in results)
    except Exception as e:
        logger.warning('presearch failed: %s', e)
        return None


# ── Main agent entry point ────────────────────────────────────────────────────

def run_agent(
    chat_id: int,
    user_message: str,
    space,
    bot_id: int | None = None,
    custom_bot=None,
    article_ids: list | None = None,
    owner=None,
) -> tuple[str, list]:
    """
    Запустить агентный цикл.
    Возвращает (answer, source_articles) где source_articles — список WikiArticle.
    Если настроен OpenAI API key — использует нативный tool calling.
    Иначе — ReAct через legacy LLM.
    """
    from recordings.models import BotChatHistory

    BotChatHistory.add(chat_id, bot_id, 'user', user_message)

    # ── Авто-уточнение при коротком запросе и низком score ──
    words = user_message.strip().split()
    if len(words) < CLARIFY_MIN_WORDS:
        max_score = _presearch(user_message, space, article_ids)
        if max_score is not None and max_score < CLARIFY_THRESHOLD:
            clarify = (
                f'🤔 Не совсем понял запрос «{user_message}».\n\n'
                f'Уточните, пожалуйста:\n'
                f'• Что именно вас интересует?\n'
                f'• В каком контексте используется этот термин?\n\n'
                f'Чем подробнее опишете — тем точнее я найду ответ в базе знаний.'
            )
            BotChatHistory.add(chat_id, bot_id, 'assistant', clarify)
            return clarify, []

    # Для custom_bot владелец — создатель бота, не собеседник
    if custom_bot and not owner:
        owner = custom_bot.owner

    history = BotChatHistory.get_history(chat_id, bot_id, last_n=HISTORY_WINDOW)
    system_prompt = _build_system_prompt(custom_bot=custom_bot, article_ids=article_ids, owner=owner)
    messages: list[dict] = [{'role': 'system', 'content': system_prompt}]

    use_openai = bool(_get_openai_key())

    for h in history:
        # Кастомные роли (audio, ocr) — не поддерживаются LLM, конвертируем в user
        if h.role in ('audio', 'ocr'):
            label = '🎙 Аудио' if h.role == 'audio' else '📷 OCR'
            content = h.content or f'[{label} — без текста]'
            messages.append({'role': 'user', 'content': f'[{label}] {content}'})
            continue
        if use_openai:
            # OpenAI: включаем tool-результаты как user-сообщения (нет tool_call_id в истории)
            if h.role == 'tool':
                messages.append({
                    'role': 'user',
                    'content': f'[Результат инструмента {h.tool_name}]\n{h.content}',
                })
                continue
            if h.role == 'assistant' and not h.content:
                continue
        msg = {'role': h.role, 'content': h.content}
        if h.role == 'tool' and h.tool_name:
            msg['name'] = h.tool_name
        messages.append(msg)

    # Трекер источников — статьи, к которым обращался агент
    source_tracker: dict[str, object] = {}
    tool_context = {'space': space, 'article_ids': article_ids, '_source_tracker': source_tracker, 'owner': owner, 'custom_bot': custom_bot}

    if use_openai:
        answer, extra_slugs = _run_openai_agent(chat_id, bot_id, messages, tool_context)
    else:
        answer, extra_slugs = _run_react_agent(chat_id, bot_id, messages, tool_context)

    # Объединяем источники: из трекера + из SOURCES: в тексте ответа
    all_slugs = set(source_tracker.keys()) | set(extra_slugs)
    sources = [source_tracker[s] for s in all_slugs if s in source_tracker]

    # Если LLM упомянул slugs которых нет в трекере — подгружаем
    missing = all_slugs - set(source_tracker.keys())
    if missing:
        from wiki_kb.models import WikiArticle
        qs = WikiArticle.objects.filter(slug__in=missing, is_deleted=False)
        if space:
            qs = qs.filter(space=space)
        sources += list(qs)

    return _clean_answer(answer), sources


def _run_openai_agent(chat_id, bot_id, messages, tool_context) -> tuple[str, list[str]]:
    """Агентный цикл через OpenAI с нативным function calling."""
    from recordings.models import BotChatHistory

    for iteration in range(MAX_ITERATIONS):
        try:
            data = _call_openai(messages, OPENAI_TOOLS)
        except Exception as e:
            body = getattr(getattr(e, 'response', None), 'text', '')
            logger.error('OpenAI call failed: %s %s', e, body[:500])
            return '⚠️ Ошибка при обращении к OpenAI.', []

        choice = data['choices'][0]
        msg = choice['message']
        finish = choice.get('finish_reason', '')

        messages.append(msg)

        if finish == 'tool_calls' and msg.get('tool_calls'):
            tool_results = []
            for tc in msg['tool_calls']:
                fn_name = tc['function']['name']
                try:
                    fn_args = json.loads(tc['function']['arguments'])
                except Exception:
                    fn_args = {}

                tool_fn = TOOLS.get(fn_name)
                logger.info('OpenAI tool call: %s(%s) chat=%s', fn_name, fn_args, chat_id)

                try:
                    result = tool_fn(fn_args, **tool_context) if tool_fn else f'Инструмент {fn_name} не найден'
                except Exception as e:
                    result = f'Ошибка: {e}'

                BotChatHistory.add(chat_id, bot_id, 'tool', result, tool_name=fn_name)
                tool_results.append({
                    'role': 'tool',
                    'tool_call_id': tc['id'],
                    'content': result,
                })

            messages.extend(tool_results)

        else:
            answer = (msg.get('content') or '').strip()
            extra_slugs = _extract_sources_from_text(answer)
            answer = _clean_answer(answer)
            if not answer:
                answer = '📭 Не удалось получить ответ.'
            BotChatHistory.add(chat_id, bot_id, 'assistant', answer)
            return answer, extra_slugs

    return 'Не удалось сформировать ответ после нескольких попыток.', []


def _run_react_agent(chat_id, bot_id, messages, tool_context) -> tuple[str, list[str]]:
    """Агентный цикл через ReAct (текстовый формат) для legacy LLM."""
    from recordings.models import BotChatHistory

    session_id = f'agent-{chat_id}-{bot_id or "main"}'

    for iteration in range(MAX_ITERATIONS):
        raw = _call_legacy_llm(messages, session_id)
        if not raw:
            break

        kind, payload = _parse_react(raw)

        if kind == 'tool' and payload:
            tool_name = payload.get('name', '')
            tool_args = payload.get('args', {})
            tool_fn = TOOLS.get(tool_name)
            logger.info('ReAct tool call: %s(%s) chat=%s', tool_name, tool_args, chat_id)

            try:
                tool_result = tool_fn(tool_args, **tool_context) if tool_fn else f'Инструмент {tool_name} не найден'
            except Exception as e:
                tool_result = f'Ошибка: {e}'

            messages.append({'role': 'assistant', 'content': raw})
            messages.append({'role': 'tool', 'name': tool_name, 'content': tool_result})
            BotChatHistory.add(chat_id, bot_id, 'assistant', raw)
            BotChatHistory.add(chat_id, bot_id, 'tool', tool_result, tool_name=tool_name)

            if iteration == MAX_ITERATIONS - 1:
                messages.append({'role': 'user', 'content': 'Дай финальный ответ. Начни с ANSWER:'})
        else:
            answer = _extract_answer(raw)
            extra_slugs = _extract_sources_from_text(answer)
            answer = _clean_answer(answer)
            BotChatHistory.add(chat_id, bot_id, 'assistant', answer)
            return answer, extra_slugs

    return 'Не удалось сформировать ответ. Попробуйте /clear и переформулируйте вопрос.', []
