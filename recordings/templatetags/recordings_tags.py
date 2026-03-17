from django import template
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

register = template.Library()


@register.filter
def in_tz(dt, tz_name):
    """Convert an aware datetime to the given timezone and format as DD.MM.YYYY HH:MM."""
    if not dt:
        return ''
    try:
        tz = ZoneInfo(tz_name or 'Europe/Moscow')
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo('Europe/Moscow')
    return dt.astimezone(tz).strftime('%d.%m.%Y %H:%M')


@register.filter
def in_tz_time(dt, tz_name):
    """Convert an aware datetime to the given timezone and format as HH:MM."""
    if not dt:
        return ''
    try:
        tz = ZoneInfo(tz_name or 'Europe/Moscow')
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo('Europe/Moscow')
    return dt.astimezone(tz).strftime('%H:%M')
