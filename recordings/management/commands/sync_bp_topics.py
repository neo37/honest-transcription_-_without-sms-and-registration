"""
Пробует отправить сообщение в каждый thread_id от 1 до 100.
Если успешно — топик существует, сохраняем в БД и сразу удаляем сообщение.
"""
import json
import time
import urllib.request
import urllib.error

from django.conf import settings
from django.core.management.base import BaseCommand

from recordings.models import BPChatTopic


def _tg(token, method, **kwargs):
    url = f'https://api.telegram.org/bot{token}/{method}'
    body = json.dumps(kwargs).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, json.loads(e.read().decode())
    except Exception as e:
        return None, str(e)


class Command(BaseCommand):
    help = 'Сканирует thread_id 1–100, создаёт BPChatTopic для найденных'

    def add_arguments(self, parser):
        parser.add_argument('--start', type=int, default=1)
        parser.add_argument('--end', type=int, default=100)
        parser.add_argument('--delay', type=float, default=1.0)

    def handle(self, *args, **options):
        token = getattr(settings, 'BP_CHAT_BOT_TOKEN', '')
        group_id = getattr(settings, 'BP_CHAT_GROUP_ID', 0)
        if not token or not group_id:
            self.stderr.write('BP_CHAT_BOT_TOKEN или BP_CHAT_GROUP_ID не заданы')
            return

        start = options['start']
        end = options['end']
        delay = options['delay']
        found = []

        for thread_id in range(start, end + 1):
            result, err = _tg(token, 'sendMessage',
                              chat_id=group_id,
                              message_thread_id=thread_id,
                              text='…')
            if result and result.get('ok'):
                msg_id = result['result']['message_id']
                # Определяем имя топика из reply_to_message
                reply = result['result'].get('reply_to_message') or {}
                fc = reply.get('forum_topic_created') or {}
                name = fc.get('name') or f'Топик {thread_id}'
                icon_color = fc.get('icon_color', 0)

                topic, created = BPChatTopic.objects.get_or_create(
                    thread_id=thread_id,
                    defaults={'name': name, 'icon_color': icon_color},
                )
                if not created and name != f'Топик {thread_id}' and topic.name.startswith('Топик '):
                    topic.name = name
                    topic.save(update_fields=['name'])

                action = 'создан' if created else 'уже есть'
                self.stdout.write(self.style.SUCCESS(
                    f'thread_id={thread_id}: "{topic.name}" [{action}]'
                ))
                found.append(thread_id)

                # Удаляем отправленное сообщение
                _tg(token, 'deleteMessage', chat_id=group_id, message_id=msg_id)
            else:
                code = (err or {}).get('error_code') if isinstance(err, dict) else None
                if code not in (400, 403):
                    self.stdout.write(f'thread_id={thread_id}: нет (err={err})')

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово. Найдено топиков: {len(found)} → {found}'
        ))
