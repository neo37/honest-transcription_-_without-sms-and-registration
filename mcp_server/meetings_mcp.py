"""
MCP сервер для управления встречами BusinessPad.

Запуск:
  python mcp_server/meetings_mcp.py

Протокол: HTTP + SSE (порт 3001).
Инструменты:
  - create_meeting(title, scheduled_at, owner_email)
  - get_available_slots(date, owner_email)
  - get_busy_slots(date, owner_email)
"""
from __future__ import annotations

import datetime
import os
import sys

# Django setup — должен быть первым
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetrec.settings')

import django
django.setup()

import uuid
import logging
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    'BusinessPad Meetings',
    instructions=(
        'Сервер управления встречами BusinessPad. '
        'Позволяет создавать встречи и просматривать свободные слоты. '
        'Занятые слоты отображаются без деталей — только статус "занято".'
    ),
)


def _get_owner(owner_email: str):
    from recordings.models import SiteUser
    user = SiteUser.objects.filter(email__iexact=owner_email).first()
    if not user:
        raise ValueError(f'Пользователь {owner_email} не найден.')
    return user


def _compute_slots(owner, date: datetime.date):
    """Возвращает (free_slots, busy_slots) списками строк HH:MM-HH:MM."""
    from recordings.models import MeetingRoom, RecurringBusyTime
    from django.utils import timezone

    day_start = datetime.time(9, 0)
    day_end = datetime.time(18, 0)
    slot_min = 30

    # Генерируем все 30-минутные слоты
    slots = []
    cur = datetime.datetime.combine(date, day_start)
    end_dt = datetime.datetime.combine(date, day_end)
    while cur + datetime.timedelta(minutes=slot_min) <= end_dt:
        slots.append((cur.time(), (cur + datetime.timedelta(minutes=slot_min)).time()))
        cur += datetime.timedelta(minutes=slot_min)

    # Занятые интервалы
    busy_intervals = []

    # 1. Повторяющиеся блоки
    weekday = date.weekday()
    for r in RecurringBusyTime.objects.filter(owner=owner, is_active=True):
        if r.repeat == 'daily' or (r.repeat == 'weekdays' and weekday < 5):
            busy_intervals.append((r.start_time, r.end_time))

    # 2. Запланированные встречи владельца на эту дату
    tz = timezone.get_current_timezone()
    d_start = timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))
    d_end = timezone.make_aware(datetime.datetime.combine(date, datetime.time.max))
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

    fmt = lambda s, e: f'{s.strftime("%H:%M")}-{e.strftime("%H:%M")}'
    free = [fmt(s, e) for s, e in slots if not overlaps(s, e)]
    busy = [fmt(s, e) for s, e in slots if overlaps(s, e)]
    return free, busy


# ── Инструменты ───────────────────────────────────────────────────────────────

@mcp.tool()
def create_meeting(title: str, scheduled_at: str, owner_email: str) -> str:
    """
    Создать встречу для пользователя.

    Args:
        title: Название встречи.
        scheduled_at: Дата и время в ISO 8601, например '2024-03-15T14:00'.
        owner_email: Email владельца (для кого создаётся встреча).

    Returns:
        Подтверждение с названием, временем и ссылкой на встречу.
    """
    from recordings.models import MeetingRoom, MeetingAttendee
    from django.utils.text import slugify
    from django.utils import timezone

    try:
        owner = _get_owner(owner_email)
    except ValueError as e:
        return str(e)

    title = title.strip()
    if not title:
        return 'Ошибка: не указано название встречи.'

    try:
        dt = datetime.datetime.fromisoformat(scheduled_at)
        if dt.tzinfo is None:
            dt = timezone.make_aware(dt)
    except (ValueError, TypeError):
        return f'Ошибка: неверный формат даты «{scheduled_at}». Используйте ISO 8601, например 2024-03-15T14:00.'

    base = slugify(title)[:40] or 'meeting'
    room_name = base
    if MeetingRoom.objects.filter(room_name=room_name).exists():
        room_name = f'{base}-{uuid.uuid4().hex[:6]}'

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

    dt_display = dt.strftime('%d.%m.%Y %H:%M')
    return (
        f'✅ Встреча создана!\n'
        f'Название: {title}\n'
        f'Время: {dt_display}\n'
        f'Ссылка: {join_url}'
    )


@mcp.tool()
def get_available_slots(date: str, owner_email: str) -> str:
    """
    Получить свободные временные слоты на дату.
    Занятые слоты отображаются только как «занято» — без деталей встреч.

    Args:
        date: Дата в формате YYYY-MM-DD.
        owner_email: Email владельца календаря.

    Returns:
        Список свободных и занятых слотов (по 30 минут, 9:00-18:00).
    """
    try:
        owner = _get_owner(owner_email)
    except ValueError as e:
        return str(e)

    try:
        d = datetime.date.fromisoformat(date)
    except (ValueError, TypeError):
        return f'Ошибка: неверный формат даты «{date}». Используйте YYYY-MM-DD.'

    free, busy = _compute_slots(owner, d)
    day_names = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
    day_display = f'{d.strftime("%d.%m.%Y")} ({day_names[d.weekday()]})'

    if not free and not busy:
        return f'На {day_display} рабочих слотов нет.'

    lines = [f'📅 {day_display}']
    if free:
        lines.append(f'\n✅ Свободно: {", ".join(free)}')
    else:
        lines.append('\n✅ Свободных слотов нет')
    if busy:
        lines.append(f'🔴 Занято: {", ".join(busy)}')

    return '\n'.join(lines)


@mcp.tool()
def get_busy_slots(date: str, owner_email: str) -> str:
    """
    Получить только занятые слоты на дату (без деталей).

    Args:
        date: Дата в формате YYYY-MM-DD.
        owner_email: Email владельца календаря.

    Returns:
        Список занятых временных интервалов.
    """
    try:
        owner = _get_owner(owner_email)
    except ValueError as e:
        return str(e)

    try:
        d = datetime.date.fromisoformat(date)
    except (ValueError, TypeError):
        return f'Ошибка: неверный формат даты «{date}». Используйте YYYY-MM-DD.'

    _, busy = _compute_slots(owner, d)
    if not busy:
        return f'На {d.strftime("%d.%m.%Y")} занятых слотов нет.'
    return f'Занятые слоты: {", ".join(busy)}'


if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', '3001'))
    mcp.run(transport='sse', host='0.0.0.0', port=port)
