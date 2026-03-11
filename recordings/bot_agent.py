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

def _build_system_prompt(custom_bot=None, article_ids=None) -> str:
    section = ''
    if custom_bot and custom_bot.root_article:
        section = f'\nТы специализируешься на разделе «{custom_bot.root_article.title}».'

    return f"""Ты AI-агент компании с доступом к корпоративной базе знаний.{section}

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

def _tool_search_wiki(args: dict, space, article_ids=None, _source_tracker=None) -> str:
    from wiki_kb.models import wiki_semantic_search
    query = args.get('query', '')
    if not query:
        return 'Ошибка: не указан запрос.'
    results = wiki_semantic_search(query, space=space, article_ids=article_ids, top_k=4)
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


def _tool_get_article(args: dict, space, article_ids=None, _source_tracker=None) -> str:
    from wiki_kb.models import WikiArticle
    slug = args.get('slug', '')
    if not slug:
        return 'Ошибка: не указан slug.'
    qs = WikiArticle.objects.filter(slug=slug, is_deleted=False)
    if space:
        qs = qs.filter(space=space)
    art = qs.first()
    if not art:
        return f'Статья «{slug}» не найдена.'
    if _source_tracker is not None:
        _source_tracker[slug] = art
    content = art.content[:3000]
    if len(art.content) > 3000:
        content += '\n\n...[обрезано]'
    return f'# {art.title}\n\n{content}'


def _tool_list_articles(args: dict, space, article_ids=None, **__) -> str:
    from wiki_kb.models import WikiArticle
    qs = WikiArticle.objects.filter(is_deleted=False).order_by('order', 'title')
    if space:
        qs = qs.filter(space=space)
    if article_ids:
        qs = qs.filter(pk__in=article_ids)
    arts = qs[:30]
    if not arts:
        return 'Статей не найдено.'
    return '\n'.join(f'- [{a.slug}] {a.title}' for a in arts)


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


TOOLS = {
    'search_wiki': _tool_search_wiki,
    'get_article': _tool_get_article,
    'list_articles': _tool_list_articles,
    'list_meetings': _tool_list_meetings,
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
]


# ── LLM backends ──────────────────────────────────────────────────────────────

def _get_openai_key() -> str:
    from recordings.models import SystemConfig
    return SystemConfig.get('openai_api_key', '')


def _get_openai_model() -> str:
    from recordings.models import SystemConfig
    return SystemConfig.get('openai_model', 'gpt-4o-mini')


def _call_openai(messages: list[dict], tools: list) -> dict:
    """Вызов OpenAI API с нативным tool calling. Возвращает dict с полем choice."""
    import requests as req
    key = _get_openai_key()
    resp = req.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={
            'model': _get_openai_model(),
            'messages': messages,
            'tools': tools,
            'tool_choice': 'auto',
            'temperature': 0.3,
        },
        timeout=90,
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

    history = BotChatHistory.get_history(chat_id, bot_id, last_n=HISTORY_WINDOW)
    system_prompt = _build_system_prompt(custom_bot=custom_bot, article_ids=article_ids)
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
    tool_context = {'space': space, 'article_ids': article_ids, '_source_tracker': source_tracker}

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
