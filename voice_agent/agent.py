"""LiveKit Voice Agent «Маскот» (livekit-agents 1.x).
VAD: Silero | STT: faster-whisper | wake-word: «маскот»/«феликс» | LLM: r-ai BP | TTS: Silero
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import aiohttp
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    UserInputTranscribedEvent,
)
from livekit.agents.voice import room_io
from livekit.plugins import silero as silero_plugin

from tts_silero import SileroTTS
from stt_whisper import WhisperSTT
from llm_bp import BPLLM
from log_reporter import report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WAKE_WORD     = os.environ.get("AGENT_WAKE_WORD", "маскот|феликс")
AGENT_NAME    = os.environ.get("AGENT_NAME", "Маскот")
LK_URL        = os.environ["LIVEKIT_URL"]
LK_API_KEY    = os.environ["LIVEKIT_API_KEY"]
LK_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
DJANGO_URL    = os.environ.get("DJANGO_URL", "https://baza.business-pad.com")
MASTER_API_KEY = os.environ.get("MASTER_API_KEY", "")

SYSTEM_PROMPT = os.environ.get(
    "AGENT_SYSTEM_PROMPT",
    f"Ты голосовой ассистент по имени {AGENT_NAME}. "
    "Отвечай коротко и по делу. Говори только на русском языке. "
    "Не добавляй лишних вступлений.",
)

_WAKE_RE  = re.compile(WAKE_WORD, re.IGNORECASE)
_EXIT_RE  = re.compile(
    r"(?:^exit\b|уйди|выгнать|выгони|убирайся|покинь|покиньте|уходи|пошёл вон|пошел вон|бывай)",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF"
    r"\U0001F600-\U0001F64F\U0001F900-\U0001F9FF]+",
    re.UNICODE,
)
_HISTORY_RE = re.compile(
    r"истори|что говорил|о чём говорил|о чем говорил|перескажи|резюм|итог|summary|последн.{1,10}минут",
    re.IGNORECASE,
)
_MINUTES_RE = re.compile(r"последн\S*\s+(\d+)\s*минут", re.IGNORECASE)
_TASK_RE = re.compile(
    r"создай задач|поставь задач|добавь задач|запиши задач|новая задача|задача:",
    re.IGNORECASE,
)
# "по последним N минутам создай задачи" — задачи из истории чата
_TASK_FROM_HISTORY_RE = re.compile(
    r"(?:по\s+)?(?:последн\S*\s+(\d+)\s*минут\S*|(?:всей?\s+)?истори\S*)\s+(?:чата?\s+)?создай\s+задач",
    re.IGNORECASE,
)


def _strip_wake(text: str) -> str:
    return _WAKE_RE.sub("", text).strip(" ,!.?") or text


async def _send_chat(room: rtc.Room, text: str) -> None:
    """Отправляет текст в чат комнаты от имени агента."""
    payload = json.dumps({
        "message": text,
        "timestamp": int(time.time() * 1000),
        "id": str(uuid.uuid4()),
    }).encode("utf-8")
    try:
        await room.local_participant.publish_data(payload, reliable=True, topic="lk-chat-topic")
    except Exception as e:
        logger.debug("chat send error: %s", e)


def _extract_task_title(query: str) -> str:
    """Извлекает название задачи из фразы."""
    # Убираем паттерн "создай задачу" и берём остаток
    cleaned = _TASK_RE.sub("", query).strip(" :,!.")
    return cleaned or query


def _build_history_prompt(chat_buffer: deque, query: str) -> str:
    now = time.time()
    m = _MINUTES_RE.search(query)
    minutes = int(m.group(1)) if m else None
    cutoff = (now - minutes * 60) if minutes else None
    entries = [e for e in chat_buffer if cutoff is None or e["ts"] >= cutoff]
    if not entries:
        period = f"последние {minutes} минут" if minutes else "всю встречу"
        return f"За {period} сообщений в чате не было."
    lines = []
    for e in entries:
        dt = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M:%S")
        lines.append(f"[{dt}] {e['sender']}: {e['text']}")
    history_text = "\n".join(lines)
    period = f"за последние {minutes} минут" if minutes else "за всё время встречи"
    return (
        f"Вот история чата {period}:\n\n{history_text}\n\n"
        f"Пользователь просит: {query}. Сделай краткое резюме или ответь на вопрос."
    )


async def _create_task(room: str, title: str, speaker: str) -> bool:
    """Создаёт задачу через Django API."""
    if not MASTER_API_KEY:
        return False
    url = f"{DJANGO_URL.rstrip('/')}/api/mascot-task/"
    try:
        async with aiohttp.ClientSession() as s:
            resp = await s.post(
                url,
                json={"room": room, "title": title, "speaker": speaker},
                headers={"X-Agent-Key": MASTER_API_KEY},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            return resp.status == 200
    except Exception as e:
        logger.debug("task create error: %s", e)
        return False


async def entrypoint(ctx: JobContext):
    room_name = ctx.room.name
    logger.info("Маскот входит в комнату: %s", room_name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    await report(room_name, "joined", "Маскот подключился к комнате")

    chat_buffer: deque[dict] = deque(maxlen=500)

    session = AgentSession(
        vad=silero_plugin.VAD.load(),
        stt=WhisperSTT(),
        llm=BPLLM(),
        tts=SileroTTS(speaker=os.environ.get("TTS_SPEAKER", "xenia")),
        allow_interruptions=True,
        min_endpointing_delay=0.6,
    )

    # ── Перехватываем ответы агента → пишем в чат ────────────────────────────
    @session.on("conversation_item_added")
    def on_item_added(ev):
        item = ev.item
        if getattr(item, "role", None) != "assistant":
            return
        content = item.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c.text if hasattr(c, "text") else str(c) for c in content)
        else:
            return
        text = text.strip()
        if not text:
            return
        asyncio.create_task(report(room_name, "said", text))
        asyncio.create_task(_send_chat(ctx.room, text))

    # ── Голосовой wake word ───────────────────────────────────────────────────
    @session.on("user_input_transcribed")
    def on_voice(ev: UserInputTranscribedEvent):
        if not ev.is_final:
            return
        text = ev.transcript.strip()
        if not text:
            return
        asyncio.create_task(report(room_name, "heard", text))

        if not _WAKE_RE.search(text):
            session.clear_user_turn()
            return

        query = _strip_wake(text)
        asyncio.create_task(report(room_name, "wake", query))
        logger.info("Голос | wake: %r", query)

        if _TASK_RE.search(query):
            asyncio.create_task(_handle_task(query, speaker=""))
        elif _HISTORY_RE.search(query):
            session.generate_reply(user_input=_build_history_prompt(chat_buffer, query))
        else:
            session.generate_reply(user_input=query if query else None)

    # ── Чат: буфер + wake word + emoji + exit ────────────────────────────────
    @ctx.room.on("data_received")
    def on_data(packet: rtc.DataPacket):
        try:
            msg = json.loads(packet.data.decode("utf-8", errors="ignore"))
        except Exception:
            return

        text = (msg.get("msg") or msg.get("message") or msg.get("text") or "").strip()
        if not isinstance(text, str) or not text:
            return

        sender = getattr(packet, "participant_identity", "") or ""
        if sender == ctx.room.local_participant.identity:
            return

        chat_buffer.append({"ts": time.time(), "sender": sender or "Участник", "text": text})

        if _EXIT_RE.search(text):
            logger.info("Чат | exit от %s", sender)
            asyncio.create_task(report(room_name, "chat", "exit — Маскот покидает комнату", sender))
            asyncio.create_task(_leave(ctx, session))
            return

        asyncio.create_task(report(room_name, "chat", text, sender))

        if not _WAKE_RE.search(text):
            if _EMOJI_RE.search(text):
                emojis = "".join(_EMOJI_RE.findall(text))
                asyncio.create_task(report(room_name, "emoji", emojis, sender))
                session.generate_reply(
                    user_input=f"Участник отправил реакцию: {emojis}. Прокомментируй коротко."
                )
            return

        query = _strip_wake(text)
        asyncio.create_task(report(room_name, "wake", query, sender))
        logger.info("Чат | wake: %r от %s", query, sender)

        if _TASK_RE.search(query):
            asyncio.create_task(_handle_task(query, speaker=sender))
        elif _HISTORY_RE.search(query):
            session.generate_reply(user_input=_build_history_prompt(chat_buffer, query))
        elif query:
            session.generate_reply(user_input=query)
        else:
            session.generate_reply(user_input=f"Тебя позвали в чате: {text}")

    async def _handle_task(query: str, speaker: str):
        # Проверяем: задачи из истории чата?
        if _TASK_FROM_HISTORY_RE.search(query):
            await _handle_tasks_from_history(query, speaker)
            return
        title = _extract_task_title(query)
        if not title:
            session.generate_reply(user_input="Скажи мне: что записать как задачу?")
            return
        ok = await _create_task(room_name, title, speaker)
        if ok:
            reply = f"Записал задачу: {title}"
        else:
            reply = f"Запомнил задачу: {title}. Но не смог сохранить в систему."
        await session.say(reply, allow_interruptions=False)
        asyncio.create_task(_send_chat(ctx.room, f"✅ Задача записана: {title}"))

    async def _handle_tasks_from_history(query: str, speaker: str):
        """Извлекает задачи из истории чата через LLM и сохраняет их."""
        # Определяем временной фильтр
        m = _MINUTES_RE.search(query)
        minutes = int(m.group(1)) if m else None
        now = time.time()
        cutoff = (now - minutes * 60) if minutes else None
        entries = [e for e in chat_buffer if cutoff is None or e["ts"] >= cutoff]

        if not entries:
            await session.say("В истории чата нет сообщений за этот период.", allow_interruptions=False)
            return

        lines = [f"{e['sender']}: {e['text']}" for e in entries]
        history_text = "\n".join(lines)
        period = f"за последние {minutes} минут" if minutes else "за всё время"

        # Запрос к LLM: извлечь задачи списком
        llm_prompt = (
            f"Вот история чата {period}:\n\n{history_text}\n\n"
            "Вычли из этого чата список задач (action items, что нужно сделать). "
            "Отвечай ТОЛЬКО списком задач, каждая с новой строки, начиная с «- ». "
            "Если задач нет — напиши «Задач нет». Отвечай на русском."
        )

        # Используем llm_bp напрямую
        from llm_bp import _call_llm
        raw = await _call_llm(llm_prompt)

        if not raw or "задач нет" in raw.lower():
            await session.say("В этом отрезке разговора задач не нашёл.", allow_interruptions=False)
            asyncio.create_task(_send_chat(ctx.room, "🔍 Задач в этом отрезке не найдено."))
            return

        # Парсим список
        task_titles = []
        for line in raw.split("\n"):
            line = line.strip().lstrip("-•*").strip()
            if line:
                task_titles.append(line)

        created = 0
        for title in task_titles:
            ok = await _create_task(room_name, title, speaker)
            if ok:
                created += 1

        summary = f"Записал {created} задач" + ("" if created == 1 else "и" if created in (2, 3, 4) else "")
        await session.say(summary, allow_interruptions=False)
        tasks_text = "\n".join(f"✅ {t}" for t in task_titles)
        asyncio.create_task(_send_chat(ctx.room, f"📋 Задачи из чата {period}:\n{tasks_text}"))

    agent = Agent(instructions=SYSTEM_PROMPT)
    await session.start(
        agent,
        room=ctx.room,
        room_input_options=room_io.RoomInputOptions(text_enabled=False),
    )
    logger.info("Маскот готов в комнате: %s", room_name)

    voice_greeting = "Привет! Я Маскот Феликс. Посмотри что я умею — написал в чат."
    chat_greeting = (
        "👋 Привет! Я **Маскот Феликс**, голосовой ассистент.\n"
        "Зови меня по имени голосом или в чате — отвечу.\n\n"
        "Что умею:\n"
        "• Ответить на любой вопрос\n"
        "• Резюме чата: «Феликс, расскажи о чём говорили»\n"
        "• Резюме за период: «Феликс, последние 5 минут»\n"
        "• Создать задачу: «Феликс, создай задачу: позвонить Ивану»\n"
        "• Задачи из чата: «Феликс, по последним 10 минутам создай задачи»\n\n"
        "Чтобы выгнать: напиши `exit`, `уйди` или `выгнать`"
    )
    await session.say(voice_greeting, allow_interruptions=False)
    await report(room_name, "said", voice_greeting)
    await asyncio.sleep(0.5)
    asyncio.create_task(_send_chat(ctx.room, chat_greeting))


async def _leave(ctx: JobContext, session: AgentSession):
    bye = "Хорошо, ухожу. До встречи!"
    await session.say(bye, allow_interruptions=False)
    await report(ctx.room.name, "said", bye)
    asyncio.create_task(_send_chat(ctx.room, bye))
    await asyncio.sleep(2)
    await session.aclose()
    await ctx.room.disconnect()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            api_key=LK_API_KEY,
            api_secret=LK_API_SECRET,
            ws_url=LK_URL,
            agent_name=AGENT_NAME,
        )
    )
