import logging
import re
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import Recording, PollLog, Space, AIConfig
from .s3_client import list_mp3_files, download_mp3_to_local
from .queue_services import enqueue_transcribe, enqueue_embedding, get_next_transcribe_task, get_next_embedding_task, index_embedding_for_recording

logger = logging.getLogger(__name__)


def _extract_space_slug(s3_key: str):
    """Ищет паттерн -org-{slug} в конце ключа S3."""
    m = re.search(r'-org-([a-z0-9\-]+?)(?:\.mp3)?$', s3_key, re.IGNORECASE)
    return m.group(1) if m else None


def run_poll_and_transcribe():
    """
    Один цикл: опрос S3, обновление стабильности, добавление стабильных в очередь.
    Транскрибация выполняется воркером из очереди (run_poller обрабатывает очередь).
    """
    log = PollLog.objects.create(success=True)
    try:
        files = list_mp3_files()
        log.files_found = len(files)
        log.save()

        now = timezone.now()
        stable_delta = timezone.timedelta(seconds=settings.STABLE_SIZE_SECONDS)

        for item in files:
            key = item['key']
            size = item['size']
            filename = key.split('/')[-1] if '/' in key else key

            rec, created = Recording.objects.get_or_create(
                s3_key=key,
                defaults={
                    'filename': filename,
                    'size_bytes': size,
                    'last_size_check_at': now,
                    'size_stable_since': None,
                    'status': Recording.Status.PENDING,
                },
            )
            if created:
                slug = _extract_space_slug(key)
                if slug:
                    space = Space.objects.filter(slug=slug).first()
                else:
                    space = Space.objects.filter(slug=getattr(settings, 'BP_SPACE_SLUG', 'org-bp')).first()
                if space:
                    rec.space = space
                    rec.save(update_fields=['space'])
                continue

            if rec.status in (Recording.Status.DONE, Recording.Status.FAILED, Recording.Status.TRANSCRIBING):
                continue

            new_size = size
            rec.last_size_check_at = now

            if new_size != rec.size_bytes:
                rec.size_bytes = new_size
                rec.size_stable_since = None
            else:
                if rec.size_stable_since is None:
                    rec.size_stable_since = now
                if (now - rec.size_stable_since) >= stable_delta:
                    rec.status = Recording.Status.STABLE
                    log.files_stable += 1
                    enqueue_transcribe(rec, priority=0)

            rec.save()

        log.save()
        log.finished_at = timezone.now()
        log.message = f'Найдено {log.files_found}, стабильных {log.files_stable}'
        log.save()
        return log
    except Exception as e:
        logger.exception('Poll failed')
        log.success = False
        log.message = str(e)
        log.finished_at = timezone.now()
        log.save()
        raise


def transcribe_one(rec: Recording):
    """Скачать MP3, выполнить faster-whisper, сохранить транскрипцию. После успеха — в очередь эмбеддингов."""
    rec.status = Recording.Status.TRANSCRIBING
    rec.error_message = ''
    rec.save(update_fields=['status', 'error_message'])

    download_dir = Path(settings.RECORDINGS_DOWNLOAD_DIR)
    download_dir.mkdir(parents=True, exist_ok=True)
    safe_name = rec.s3_key.replace('/', '_')
    local_path = download_dir / safe_name

    try:
        download_mp3_to_local(rec.s3_key, local_path)
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(local_path), language="ru", beam_size=5)
        text = ' '.join(s.text for s in segments).strip()
        rec.transcription = text
        rec.transcribed_at = timezone.now()
        rec.status = Recording.Status.DONE
        rec.error_message = ''
    except Exception as e:
        logger.exception('Transcribe failed for %s', rec.s3_key)
        rec.status = Recording.Status.FAILED
        rec.error_message = str(e)
    finally:
        rec.save()
        if local_path.exists():
            try:
                local_path.unlink()
            except OSError:
                pass
        if rec.status == Recording.Status.DONE:
            generate_ai_summary(rec)
            enqueue_embedding(rec)


def call_llm_api(prompt: str, session_id: str, log_id: str, timeout: int = 120) -> str:
    """
    Вызов LLM API с настройками из AIConfig (DB-синглтон).
    Возвращает текстовый ответ или пустую строку при ошибке.
    """
    import requests as req
    import json as _json

    cfg = AIConfig.get()

    # Рендерим шаблон тела запроса
    try:
        # Экранируем значения для безопасной вставки внутрь JSON-строки
        prompt_escaped = _json.dumps(prompt)[1:-1]
        session_id_escaped = _json.dumps(session_id)[1:-1]
        log_id_escaped = _json.dumps(log_id)[1:-1]
        body_str = (
            cfg.request_template
            .replace('{prompt}', prompt_escaped)
            .replace('{session_id}', session_id_escaped)
            .replace('{log_id}', log_id_escaped)
        )
        payload = _json.loads(body_str)
    except Exception as e:
        logger.error("LLM request_template render/parse error: %s", e)
        return ''

    headers = {'Content-Type': 'application/json'}
    if cfg.auth_header:
        headers['Authorization'] = cfg.auth_header
    if cfg.referer:
        headers['Referer'] = cfg.referer

    try:
        resp = req.post(cfg.url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("LLM API request failed: %s", e)
        return ''

    # Извлекаем ответ по cfg.response_key
    key = cfg.response_key or 'messages'
    val = data.get(key)
    if isinstance(val, list):
        ai_res = val[-1] if val else ''
    elif isinstance(val, str):
        ai_res = val
    else:
        ai_res = data.get('response', '') or ''

    return (ai_res or '').strip()


def generate_ai_summary(rec: Recording):
    """Генерация названия и краткого саммари через внешний AI API. При любой ошибке — логируем и выходим."""
    text = (rec.transcription or '').strip()
    if not text:
        return
    try:
        import json
        first = text[:1000]
        last = text[-1000:] if len(text) > 1000 else ""
        mid = ""
        if len(text) > 3000:
            mid_point = len(text) // 2
            mid = text[mid_point - 500 : mid_point + 500]
        elif len(text) > 1000:
            mid_pos = len(text) // 2
            mid = text[mid_pos - 250 : mid_pos + 250]
        sample = f"START: {first}\n\nMIDDLE: {mid}\n\nEND: {last}"
        prompt = (
            "Ниже приведены отрывки из транскрибации встречи (начало, середина, конец). "
            "Дай встрече подходящее название и краткое саммари (о чем была встреча). "
            "Ответ должен быть СТРОГО в формате JSON: {\"title\": \"...\", \"summary\": \"...\"}. "
            f"Текст:\n\n{sample}"
        )
        ai_res = call_llm_api(
            prompt=prompt,
            session_id=f"meet_{rec.pk}_thread",
            log_id=f"meet_{rec.pk}",
            timeout=90,
        )
        if not ai_res:
            return
        try:
            clean_res = ai_res
            if clean_res.startswith('```json'):
                clean_res = clean_res.split('```json')[1].split('```')[0].strip()
            elif clean_res.startswith('```'):
                clean_res = clean_res.split('```')[1].split('```')[0].strip()
            res_json = json.loads(clean_res)
            rec.ai_title = (res_json.get('title') or '')[:255]
            rec.ai_summary = res_json.get('summary') or ''
            rec.save(update_fields=['ai_title', 'ai_summary'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse AI JSON response for rec %s: %s", rec.pk, e)
    except Exception:
        logger.exception("AI Title/Summary generation failed for rec %s", rec.pk)

def process_one_transcribe():
    """Взять одну задачу из очереди транскрибации и выполнить. Возвращает True, если задача была."""
    with transaction.atomic():
        task = get_next_transcribe_task()
        if not task:
            return False
        rec = task.recording
        task.delete()
    if rec.status in (Recording.Status.DONE, Recording.Status.FAILED, Recording.Status.TRANSCRIBING):
        return True
    transcribe_one(rec)
    return True


def process_one_embedding():
    """Взять одну задачу из очереди эмбеддингов и выполнить. Возвращает True, если задача была."""
    with transaction.atomic():
        task = get_next_embedding_task()
        if not task:
            return False
        rec = task.recording
        task.delete()
    index_embedding_for_recording(rec)
    return True
