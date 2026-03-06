"""
Вызов внешнего OCR API.
Ожидаемый контракт: POST multipart/form-data с полем "file" или "document";
ответ: JSON с полем "markdown" или "text", либо plain text.
"""
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def call_ocr_api(file_path, api_url=None, api_key=None, timeout=None, method="auto"):
    """
    Отправить файл на OCR API, вернуть (markdown_text, error_message).
    method: "auto" | "tesseract" | "olmocr"
    """
    from django.conf import settings

    url = api_url or getattr(settings, 'OCR_API_URL', '')
    if not url:
        return '', 'OCR_API_URL не задан. Укажите в .env переменную OCR_API_URL (адрес API).'

    key = api_key if api_key is not None else getattr(settings, 'OCR_API_KEY', '') or ''
    timeout = timeout if timeout is not None else getattr(settings, 'OCR_API_TIMEOUT', 300)
    path = Path(file_path)
    if not path.exists():
        return '', 'Файл не найден.'

    headers = {}
    if key:
        headers['Authorization'] = f'Bearer {key}'

    params = {'method': method}

    try:
        with open(path, 'rb') as f:
            files = {'file': (path.name, f, 'application/octet-stream')}
            resp = requests.post(
                url,
                files=files,
                headers=headers,
                params=params,
                timeout=timeout,
            )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return '', f'Таймаут запроса к OCR API ({timeout} с).'
    except requests.exceptions.RequestException as e:
        msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                body = e.response.text[:500]
                msg = f'{e.response.status_code}: {body}'
            except Exception:
                pass
        logger.exception('OCR API request failed')
        return '', msg

    content_type = (resp.headers.get('Content-Type') or '').lower()
    text = resp.text

    if 'application/json' in content_type:
        try:
            data = resp.json()
            markdown = data.get('markdown') or data.get('text') or data.get('result') or ''
            if isinstance(markdown, str):
                return markdown, ''
            return '', 'Ответ API: поле markdown/text не строка.'
        except (ValueError, TypeError) as e:
            return '', f'Некорректный JSON от API: {e}'

    return text.strip(), ''
