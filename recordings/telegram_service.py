import logging
import random
import string

import requests as req
from django.conf import settings
from django.utils.text import slugify

logger = logging.getLogger(__name__)


def _api(method, **kwargs):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        logger.warning('TELEGRAM_BOT_TOKEN not configured')
        return None
    url = f'https://api.telegram.org/bot{token}/{method}'
    try:
        r = req.post(url, json=kwargs, timeout=10)
        data = r.json()
        if not data.get('ok'):
            logger.error('TG API %s error: %s', method, data.get('description'))
            return None
        return data.get('result')
    except Exception as e:
        logger.error('TG API %s failed: %s', method, e)
        return None


def send_message(chat_id, text):
    return _api('sendMessage', chat_id=chat_id, text=text, parse_mode='Markdown')


def get_bot_info():
    return _api('getMe') or {}


def register_webhook(webhook_url):
    return _api('setWebhook', url=webhook_url, allowed_updates=['message'])


def _random_suffix(n=12):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=n))


def make_unique_space_slug(org_name):
    from recordings.models import Space
    base = slugify(org_name) or 'org'
    while True:
        slug = f'{base}-{_random_suffix(12)}'
        if not Space.objects.filter(slug=slug).exists():
            return slug


def _do_verify(chat_id, code, OrgRegistration, SiteUser, Space, generate_password):
    """Общая логика верификации кода (для /start и /verify)."""
    # Один Telegram — одна организация
    if OrgRegistration.objects.filter(tg_chat_id=chat_id, status=OrgRegistration.STATUS_VERIFIED).exists():
        send_message(chat_id, 'Эта учётная запись Telegram уже связана с организацией. Используйте /password для получения пароля.')
        return

    try:
        reg = OrgRegistration.objects.get(verify_code=code, status=OrgRegistration.STATUS_PENDING)
    except OrgRegistration.DoesNotExist:
        send_message(chat_id, 'Код не найден или уже использован.')
        return

    email = reg.email.lower()
    if SiteUser.objects.filter(email__iexact=email).exists():
        send_message(chat_id, 'Пользователь с таким email уже зарегистрирован.')
        return

    slug = make_unique_space_slug(reg.org_name)
    space = Space.objects.create(name=reg.org_name, slug=slug)

    pwd = generate_password()
    user = SiteUser.objects.create(email=email, space=space, free_left=5)
    user.set_password(pwd)
    user.save(update_fields=['password'])

    reg.status = OrgRegistration.STATUS_VERIFIED
    reg.tg_chat_id = chat_id
    reg.space = space
    reg.save(update_fields=['status', 'tg_chat_id', 'space'])

    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')

    # Генерируем magic-ссылку для автоматической авторизации
    import datetime as _dt
    from django.utils import timezone as _tz
    from recordings.models import MagicLoginToken
    magic = MagicLoginToken.objects.create(
        user=user,
        expires_at=_tz.now() + _dt.timedelta(days=7),
    )
    magic_url = f'{site_url}/magic-login/{magic.token}/' if site_url else f'/magic-login/{magic.token}/'

    send_message(
        chat_id,
        f'*Организация {reg.org_name} зарегистрирована!*\n\n'
        f'Email: `{email}`\n'
        f'Пароль: `{pwd}`\n\n'
        f'*Войти одним кликом:*\n{magic_url}\n\n'
        f'_(ссылка действует 7 дней)_\n\n'
        f'Новый пароль: /password {email}',
    )


def _do_bp_tg_verify(chat_id, code):
    """Верификация BP-пользователя через Telegram (первый вход)."""
    from recordings.models import SiteUser
    from django.utils import timezone

    try:
        user = SiteUser.objects.get(tg_verify_code=code)
    except SiteUser.DoesNotExist:
        send_message(chat_id, 'Код не найден или уже использован.')
        return

    if user.tg_verify_expires and timezone.now() > user.tg_verify_expires:
        send_message(chat_id, 'Срок действия кода истёк. Запросите новый код на сайте.')
        return

    if user.tg_verified:
        send_message(chat_id, 'Аккаунт уже подтверждён. Вернитесь на сайт и задайте пароль.')
        return

    user.tg_verified = True
    user.save(update_fields=['tg_verified'])
    send_message(
        chat_id,
        f'*Аккаунт подтверждён!*\n\n'
        f'Email: `{user.email}`\n\n'
        f'Вернитесь на сайт и задайте пароль для входа.',
    )


def handle_tg_update(data):
    """Обработать входящий Telegram webhook update."""
    from recordings.models import OrgRegistration, SiteUser, Space
    from recordings.email_service import generate_password

    message = data.get('message') or data.get('edited_message')
    if not message:
        return

    chat_id = message.get('chat', {}).get('id')
    text = (message.get('text') or '').strip()
    if not chat_id or not text:
        return

    parts = text.split()
    command = parts[0].lower().split('@')[0]  # убираем @botname если есть

    if command == '/start':
        # Deep-link: /start CODE — auto-verify
        if len(parts) >= 2:
            code = parts[1].strip()
            if code.startswith('BP'):
                _do_bp_tg_verify(chat_id, code)
            else:
                _do_verify(chat_id, code, OrgRegistration, SiteUser, Space, generate_password)
        else:
            send_message(
                chat_id,
                '*Привет!* Я помогаю с регистрацией организаций на платформе MeetRec.\n\n'
                'Команды:\n'
                '- /verify КОД — подтвердить регистрацию\n'
                '- /password EMAIL — получить новый пароль',
            )

    elif command == '/verify':
        if len(parts) < 2:
            send_message(chat_id, 'Укажите код: /verify ВАШ\\_КОД')
            return
        _do_verify(chat_id, parts[1].strip(), OrgRegistration, SiteUser, Space, generate_password)

    elif command == '/password':
        if len(parts) >= 2:
            email = parts[1].strip().lower()
        else:
            reg = OrgRegistration.objects.filter(
                tg_chat_id=chat_id, status=OrgRegistration.STATUS_VERIFIED
            ).first()
            if reg:
                email = reg.email.lower()
            else:
                send_message(chat_id, 'Укажите email: /password ваш@email.com')
                return

        try:
            user = SiteUser.objects.get(email__iexact=email)
        except SiteUser.DoesNotExist:
            send_message(chat_id, f'Пользователь {email} не найден.')
            return

        pwd = generate_password()
        user.set_password(pwd)
        user.save(update_fields=['password'])
        send_message(chat_id, f'Новый пароль для {email}:\n`{pwd}`')

    else:
        send_message(
            chat_id,
            'Команды:\n'
            '- /verify КОД — подтвердить регистрацию\n'
            '- /password EMAIL — получить новый пароль',
        )
