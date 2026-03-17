"""
BP Group Chat views — интеграция с Telegram-группой BP.
Топики группы отображаются на сайте; пользователи могут читать и писать через бота.
"""
import json
import logging
import time
import urllib.request

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth_backend import site_login_required, get_current_user
from .models import BPChatTopic, BPChatMessage

logger = logging.getLogger(__name__)

BOT_TOKEN  = getattr(settings, 'BP_CHAT_BOT_TOKEN', '')
GROUP_ID   = getattr(settings, 'BP_CHAT_GROUP_ID', 0)
BOT_ID     = int(BOT_TOKEN.split(':')[0]) if BOT_TOKEN else 0


# ── helpers ─────────────────────────────────────────────────────────────────

def _tg(method, **kwargs):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/{method}'
    body = json.dumps(kwargs).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error('TG API %s failed: %s', method, e)
        return None


def _get_or_create_topic(thread_id, name=None, icon_color=0):
    topic, created = BPChatTopic.objects.get_or_create(
        thread_id=thread_id,
        defaults={'name': name or f'Топик {thread_id}', 'icon_color': icon_color},
    )
    if not created and name and topic.name.startswith('Топик '):
        topic.name = name
        topic.save(update_fields=['name'])
    return topic


def _save_message(topic, msg_data):
    """Сохранить TG-сообщение в БД. Возвращает (obj, created)."""
    tg_id = msg_data.get('message_id')
    if not tg_id:
        return None, False
    text = msg_data.get('text') or msg_data.get('caption') or ''
    if not text:
        return None, False

    fr = msg_data.get('from') or {}
    from_tg_id = fr.get('id')
    # Игнорируем сообщения самого бота (чтобы не дублировать отправленные с сайта)
    if from_tg_id == BOT_ID:
        return None, False

    from_name = (fr.get('first_name', '') + ' ' + (fr.get('last_name') or '')).strip() or fr.get('username', 'Unknown')
    from_username = fr.get('username', '')
    tg_date = timezone.datetime.fromtimestamp(msg_data['date'], tz=timezone.utc)

    obj, created = BPChatMessage.objects.get_or_create(
        tg_message_id=tg_id,
        defaults=dict(
            topic=topic,
            from_name=from_name,
            from_tg_id=from_tg_id,
            from_username=from_username,
            text=text,
            tg_date=tg_date,
        ),
    )
    if created:
        BPChatTopic.objects.filter(pk=topic.pk).update(last_message_at=tg_date)
    return obj, created


# ── webhook ──────────────────────────────────────────────────────────────────

@csrf_exempt
def bp_webhook(request, secret):
    if secret != settings.BP_CHAT_WEBHOOK_SECRET:
        return JsonResponse({'ok': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': True})

    try:
        update = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False}, status=400)

    msg = update.get('message') or update.get('edited_message')
    if not msg:
        return JsonResponse({'ok': True})

    chat_id = msg.get('chat', {}).get('id')
    if chat_id != GROUP_ID:
        return JsonResponse({'ok': True})

    thread_id = msg.get('message_thread_id')
    if not thread_id:
        return JsonResponse({'ok': True})

    # Определяем имя топика
    topic_name = None
    fc = msg.get('forum_topic_created') or {}
    if fc:
        topic_name = fc.get('name')
        icon_color = fc.get('icon_color', 0)
    else:
        icon_color = 0
        reply = msg.get('reply_to_message') or {}
        rfc = reply.get('forum_topic_created') or {}
        if rfc:
            topic_name = rfc.get('name')
            icon_color = rfc.get('icon_color', 0)

    topic = _get_or_create_topic(thread_id, topic_name, icon_color)
    _save_message(topic, msg)

    return JsonResponse({'ok': True})


# ── views ────────────────────────────────────────────────────────────────────

@site_login_required
def bp_chat_index(request):
    topics = BPChatTopic.objects.all().order_by('-last_message_at', 'thread_id')
    # Последнее сообщение и кол-во для каждого топика
    topic_data = []
    for t in topics:
        last = t.bp_messages.order_by('-tg_date').first()
        count = t.bp_messages.count()
        topic_data.append({'topic': t, 'last': last, 'count': count})
    return render(request, 'recordings/bp_chat_index.html', {'topic_data': topic_data})


@site_login_required
def bp_chat_topic(request, thread_id):
    topic = get_object_or_404(BPChatTopic, thread_id=thread_id)
    msgs = topic.bp_messages.order_by('tg_date', 'created_at')
    all_topics = BPChatTopic.objects.order_by('-last_message_at', 'thread_id')
    return render(request, 'recordings/bp_chat_topic.html', {
        'topic': topic,
        'chat_msgs': msgs,
        'all_topics': all_topics,
    })


@site_login_required
@require_http_methods(['POST'])
def bp_chat_send(request, thread_id):
    me = get_current_user(request)
    topic = get_object_or_404(BPChatTopic, thread_id=thread_id)
    try:
        body = json.loads(request.body)
        text = (body.get('text') or '').strip()
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad json'}, status=400)
    if not text:
        return JsonResponse({'ok': False, 'error': 'empty'})

    sender_name = (me.display_name or me.email.split('@')[0]) if me else 'Гость'
    tg_text = f'*{sender_name}:* {text}'

    result = _tg('sendMessage',
                 chat_id=GROUP_ID,
                 message_thread_id=thread_id,
                 text=tg_text,
                 parse_mode='Markdown')

    if not result or not result.get('ok'):
        return JsonResponse({'ok': False, 'error': 'tg_send_failed'})

    # Сохраняем в БД сразу
    sent_msg = result.get('result', {})
    tg_msg_id = sent_msg.get('message_id')
    now = timezone.now()
    msg_obj = BPChatMessage.objects.create(
        topic=topic,
        tg_message_id=tg_msg_id,
        from_name=sender_name,
        from_site_user=me,
        text=text,
        tg_date=now,
        via_site=True,
    )
    BPChatTopic.objects.filter(pk=topic.pk).update(last_message_at=now)

    return JsonResponse({
        'ok': True,
        'id': msg_obj.pk,
        'tg_message_id': tg_msg_id,
        'created_at': now.isoformat(),
    })


@site_login_required
def bp_chat_poll(request, thread_id):
    """JSON: новые сообщения после after_id."""
    topic = get_object_or_404(BPChatTopic, thread_id=thread_id)
    after_id = int(request.GET.get('after', 0) or 0)
    msgs = list(
        topic.bp_messages.filter(pk__gt=after_id).order_by('tg_date', 'created_at')
        .values('id', 'from_name', 'from_username', 'text', 'tg_date', 'via_site')
    )
    result = []
    for m in msgs:
        result.append({
            'id': m['id'],
            'from_name': m['from_name'],
            'from_username': m['from_username'],
            'text': m['text'],
            'tg_date': m['tg_date'].isoformat() if m['tg_date'] else None,
            'via_site': m['via_site'],
        })
    return JsonResponse({'messages': result})


@site_login_required
def bp_chat_stream(request, thread_id):
    """SSE: real-time новые сообщения."""
    topic = get_object_or_404(BPChatTopic, thread_id=thread_id)
    last_id = int(request.GET.get('after', 0) or 0)
    topic_pk = topic.pk

    def generate():
        nonlocal last_id
        import json as _j
        deadline = time.time() + 25
        while time.time() < deadline:
            msgs = list(
                BPChatMessage.objects.filter(topic_id=topic_pk, pk__gt=last_id)
                .order_by('tg_date', 'created_at')
                .values('id', 'from_name', 'from_username', 'text', 'tg_date', 'via_site')
            )
            if msgs:
                for m in msgs:
                    payload = {
                        'id': m['id'],
                        'from_name': m['from_name'],
                        'from_username': m['from_username'],
                        'text': m['text'],
                        'tg_date': m['tg_date'].isoformat() if m['tg_date'] else None,
                        'via_site': m['via_site'],
                    }
                    yield f'data: {_j.dumps(payload, ensure_ascii=False)}\n\n'
                    last_id = max(last_id, m['id'])
            else:
                yield ': ping\n\n'
            time.sleep(2)

    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


def register_bp_webhook():
    """Зарегистрировать webhook для BP-чат бота."""
    if not BOT_TOKEN:
        return
    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
    if not site_url:
        return
    secret = settings.BP_CHAT_WEBHOOK_SECRET
    webhook_url = f'{site_url}/tg-bp-webhook/{secret}/'
    result = _tg('setWebhook', url=webhook_url,
                 allowed_updates=['message', 'edited_message'])
    if result and result.get('ok'):
        logger.info('BP Chat webhook registered: %s', webhook_url)
    else:
        logger.warning('BP Chat webhook registration failed: %s', result)
    return result
