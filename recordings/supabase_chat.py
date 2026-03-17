"""
Supabase REST API helper for space chat.
Uses PostgREST over HTTPS (IPv4 via Cloudflare CDN).
"""
import json
import logging
import urllib.request
import urllib.error
from django.conf import settings

logger = logging.getLogger(__name__)

SUPABASE_URL = getattr(settings, 'SUPABASE_URL', '')
SUPABASE_ANON_KEY = getattr(settings, 'SUPABASE_ANON_KEY', '')
TABLE = 'space_chat_messages'


def _headers(extra=None):
    h = {
        'apikey': SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }
    if extra:
        h.update(extra)
    return h


def _request(method, path, data=None, params=None):
    url = f'{SUPABASE_URL}/rest/v1/{path}'
    if params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{qs}'
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.error('Supabase %s %s → %s: %s', method, path, e.code, e.read()[:200])
        return None
    except Exception as e:
        logger.error('Supabase request failed: %s', e)
        return None


def fetch_messages(space_slug: str, limit: int = 60, after_id: int = 0) -> list:
    """Получить последние сообщения пространства."""
    params = {
        'space_slug': f'eq.{space_slug}',
        'order': 'id.asc',
        'limit': str(limit),
    }
    if after_id:
        params['id'] = f'gt.{after_id}'
    result = _request('GET', TABLE, params=params)
    return result if isinstance(result, list) else []


def send_message(space_slug: str, user_email: str, display_name: str,
                 tg_username: str, message: str) -> dict | None:
    """Отправить сообщение в пространство."""
    return _request('POST', TABLE, data={
        'space_slug': space_slug,
        'user_email': user_email,
        'display_name': display_name or user_email.split('@')[0],
        'tg_username': tg_username or '',
        'message': message,
    })


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)
