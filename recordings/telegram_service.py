import hashlib
import logging
import os
import random
import string

import requests as req
from django.conf import settings
from django.utils.text import slugify

logger = logging.getLogger(__name__)

SITE_URL = ''


def _site_url():
    return getattr(settings, 'SITE_URL', '').rstrip('/')


# ── Low-level Telegram API ─────────────────────────────────────────────────────

def _api(method, token=None, **kwargs):
    token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
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


def send_message(chat_id, text, token=None, reply_markup=None):
    kwargs = dict(chat_id=chat_id, text=text, parse_mode='Markdown')
    if reply_markup:
        kwargs['reply_markup'] = reply_markup
    return _api('sendMessage', token=token, **kwargs)


def send_inline(chat_id, text, buttons, token=None):
    """buttons: list of list of {text, callback_data}."""
    markup = {'inline_keyboard': buttons}
    return send_message(chat_id, text, token=token, reply_markup=markup)


def answer_callback(callback_id, text='', token=None):
    return _api('answerCallbackQuery', token=token, callback_query_id=callback_id, text=text)


def send_meeting_invite(chat_id, title, join_url):
    return send_message(
        chat_id,
        f'📅 *Вас приглашают на встречу*\n\n*{title}*\n\nПрисоединиться: {join_url}',
    )


def get_bot_info(token=None):
    return _api('getMe', token=token) or {}


def register_webhook(webhook_url, token=None):
    return _api('setWebhook', token=token, url=webhook_url, allowed_updates=['message', 'callback_query'])


# ── Helpers ───────────────────────────────────────────────────────────────────

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


_ADMIN_PASSWORD = os.environ.get('BOT_ADMIN_PASSWORD', '')  # пароль для управления ботами


def _bot_webhook_secret(token):
    return hashlib.sha256(token.encode()).hexdigest()[:32]


# ── Menu ──────────────────────────────────────────────────────────────────────

def _get_user_wiki_section(chat_id):
    """Текущий раздел вики, настроенный пользователем для главного бота."""
    from recordings.models import SystemConfig
    from wiki_kb.models import WikiArticle
    pk_str = SystemConfig.get(f'bot_section_{chat_id}', '')
    if pk_str:
        return WikiArticle.objects.filter(pk=pk_str, is_deleted=False).first()
    return None


def _attach_persistent_keyboard(chat_id, token=None):
    """Прикрепить постоянную кнопку «📌 Меню» в нижней клавиатуре Telegram."""
    _api(
        'sendMessage', token=token,
        chat_id=chat_id,
        text='‌',  # невидимый символ
        reply_markup={
            'keyboard': [['📌 Меню']],
            'resize_keyboard': True,
            'persistent': True,
        },
    )


_MAIN_BOT_ABOUT = (
    '🤖 *Что умеет этот бот:*\n\n'
    '• 💬 Отвечать на вопросы по базе знаний вашей организации (AI + wiki)\n'
    '• 📸 Распознавать текст с фото (OCR) и сохранять в вики\n'
    '• 🎙 Принимать аудио и видео файлы — ставить их в очередь транскрибации\n'
    '• 📹 Показывать активные встречи и давать ссылку для подключения\n'
    '• 🤖 Создавать дочерних ботов — каждый отвечает по своему разделу вики\n'
    '• 📖 Выбирать раздел вики для точных ответов\n\n'
    '_Просто напишите вопрос — бот найдёт ответ в базе знаний._\n'
    '_Или пришлите фото / аудио / видео._'
)


def _send_main_menu(chat_id, user, token=None, attach_keyboard=False):
    site_url = _site_url()
    section = _get_user_wiki_section(chat_id)
    wiki_url = f'{site_url}/kb/{section.slug}/' if section else f'{site_url}/kb/'
    section_label = f'📖 {section.title[:28]}' if section else '📖 Открыть вики'

    buttons = [
        [{'text': '📹 Встречи прямо сейчас', 'callback_data': 'menu:meetings'}],
        [{'text': '🗂 Выбрать раздел вики', 'callback_data': 'menu:pick_section'}],
        [{'text': '🤖 Вставить токен бота', 'callback_data': 'menu:new_bot'},
         {'text': '📋 Мои боты', 'callback_data': 'menu:my_bots'}],
        [{'text': section_label, 'url': wiki_url}],
        [{'text': 'ℹ️ О боте', 'callback_data': 'menu:about'}],
    ]
    hint = f'_Раздел: {section.title}_\n\n' if section else ''
    if attach_keyboard:
        _attach_persistent_keyboard(chat_id, token=token)
    send_inline(
        chat_id,
        f'📌 *Меню*\n\n{hint}Задайте вопрос — отвечу по базе знаний.',
        buttons,
        token=token,
    )


# ── Callback query handler ────────────────────────────────────────────────────

def _handle_callback(callback, token=None):
    from recordings.models import SiteUser, BotSetupState, CustomBot, MeetingRoom, SystemConfig

    chat_id = callback['from']['id']
    callback_id = callback['id']
    data = callback.get('data', '')

    answer_callback(callback_id, token=token)

    user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
    if not user:
        send_message(chat_id, 'Сначала авторизуйтесь.', token=token)
        return

    # ── О боте ──
    if data == 'menu:about':
        send_inline(
            chat_id,
            _MAIN_BOT_ABOUT,
            [[{'text': '◀ Назад в меню', 'callback_data': 'menu:back'}]],
            token=token,
        )

    # ── Назад в меню ──
    elif data == 'menu:back':
        _send_main_menu(chat_id, user, token=token)

    # ── Встречи ──
    elif data == 'menu:meetings':
        _send_active_meetings(chat_id, user, token=token)

    # ── Выбрать раздел вики для этого бота ──
    elif data == 'menu:pick_section':
        setup = BotSetupState.get_state(chat_id)
        setup.set('pick_section_only', pending_token='section_only', pending_bot_pk=None)
        _send_article_picker(chat_id, user, purpose='section', token=token)

    # ── Новый бот (ввести токен BotFather) ──
    elif data == 'menu:new_bot':
        setup = BotSetupState.get_state(chat_id)
        setup.set('await_password', pending_token='new_bot')
        send_message(chat_id, '🔐 Введите пароль:', token=token)

    # ── Мои боты ──
    elif data == 'menu:my_bots':
        bots = list(CustomBot.objects.filter(owner=user, is_active=True).select_related('root_article'))
        if not bots:
            send_message(chat_id, '📋 Ботов пока нет.\nНажмите «Вставить токен бота» чтобы добавить.', token=token)
            return
        site_url = _site_url()
        from recordings.models import SystemConfig
        current_model = SystemConfig.get('openai_model', 'gpt-4o-mini')
        for b in bots:
            section = b.root_article.title if b.root_article else 'Весь wiki'
            link = f'{site_url}/kb/{b.root_article.slug}/' if b.root_article else f'{site_url}/kb/'
            buttons = [
                [{'text': '🗂 Сменить страницу', 'callback_data': f'bot_change_page:{b.pk}'},
                 {'text': '🧠 Модель OpenAI', 'callback_data': f'bot_model:{b.pk}'}],
                [{'text': f'📖 {section[:35]}', 'url': link}],
                [{'text': '🗑 Удалить бота', 'callback_data': f'bot_delete:{b.pk}'}],
            ]
            send_inline(
                chat_id,
                f'🤖 *@{b.username or b.name}*\n📚 {section}\n🧠 Модель: `{current_model}`',
                buttons,
                token=token,
            )

    # ── Удалить бота (подтверждение) ──
    elif data.startswith('bot_delete:'):
        bot_pk = int(data.split(':', 1)[1])
        bot_obj = CustomBot.objects.filter(pk=bot_pk, owner=user, is_active=True).first()
        if not bot_obj:
            send_message(chat_id, 'Бот не найден.', token=token)
            return
        send_inline(
            chat_id,
            f'❓ Удалить бота *@{bot_obj.username or bot_obj.name}*?\n\nЭто действие нельзя отменить.',
            [
                [{'text': '✅ Да, удалить', 'callback_data': f'bot_delete_confirm:{bot_pk}'},
                 {'text': '❌ Отмена', 'callback_data': 'menu:my_bots'}],
            ],
            token=token,
        )

    # ── Удалить бота (подтверждено) ──
    elif data.startswith('bot_delete_confirm:'):
        bot_pk = int(data.split(':', 1)[1])
        bot_obj = CustomBot.objects.filter(pk=bot_pk, owner=user, is_active=True).first()
        if not bot_obj:
            send_message(chat_id, 'Бот не найден.', token=token)
            return
        bot_name = f'@{bot_obj.username or bot_obj.name}'
        # Отключаем webhook перед удалением
        try:
            _api('deleteWebhook', token=bot_obj.token)
        except Exception:
            pass
        bot_obj.is_active = False
        bot_obj.save(update_fields=['is_active'])
        send_message(chat_id, f'✅ Бот *{bot_name}* удалён.', token=token)

    # ── Выбор статьи из дерева ──
    elif data.startswith('pick_article:'):
        article_pk = int(data.split(':', 1)[1])
        setup = BotSetupState.get_state(chat_id)

        from wiki_kb.models import WikiArticle
        article = WikiArticle.objects.filter(pk=article_pk, space=user.space, is_deleted=False).first()
        if not article:
            send_message(chat_id, 'Статья не найдена.', token=token)
            return

        if setup.state == 'pick_section_only':
            # Сохраняем раздел для этого бота (главного)
            SystemConfig.set(f'bot_section_{chat_id}', str(article.pk))
            setup.reset()
            site_url = _site_url()
            send_inline(
                chat_id,
                f'✅ Раздел установлен: *{article.title}*\n\nТеперь я отвечаю только по этому разделу.',
                [[{'text': f'📖 Открыть «{article.title[:30]}»', 'url': f'{site_url}/kb/{article.slug}/'}]],
                token=token,
            )

        elif setup.state == 'pick_section_for_bot' and setup.pending_bot_pk:
            # Меняем страницу у существующего бота (из "Мои боты") + уведомляем пользователей
            try:
                bot = CustomBot.objects.get(pk=setup.pending_bot_pk, owner=user)
                bot.root_article = article
                bot.save(update_fields=['root_article'])
                setup.reset()
                site_url = _site_url()
                # Уведомляем всех пользователей этого бота
                from recordings.models import BotChatHistory
                chat_ids = BotChatHistory.objects.filter(
                    bot_id=bot.pk,
                ).values_list('chat_id', flat=True).distinct()
                notif_text = (
                    f'📢 Раздел бота обновлён!\n\n'
                    f'Теперь я работаю по разделу *{article.title}*.\n'
                    f'Задайте вопрос по новому разделу!'
                )
                import threading
                def _notify():
                    for cid in chat_ids:
                        try:
                            send_message(cid, notif_text, token=bot.token)
                        except Exception:
                            pass
                threading.Thread(target=_notify, daemon=True).start()
                send_inline(
                    chat_id,
                    f'✅ Страница бота *@{bot.username}* изменена на *{article.title}*.\n\n'
                    f'Уведомление разослано пользователям.',
                    [[{'text': f'📖 {article.title[:35]}', 'url': f'{site_url}/kb/{article.slug}/'}]],
                    token=token,
                )
            except Exception as e:
                logger.error('pick_section_for_bot error: %s', e)
                send_message(chat_id, 'Ошибка. Попробуйте заново.', token=token)

        elif setup.pending_bot_pk:
            # Привязываем раздел к только что созданному боту
            try:
                bot = CustomBot.objects.get(pk=setup.pending_bot_pk, owner=user)
                bot.root_article = article
                bot.save(update_fields=['root_article'])
                setup.reset()
                webhook_url = f'{_site_url()}/tg-custom/{bot.pk}/{bot.webhook_secret}/'
                register_webhook(webhook_url, token=bot.token)
                site_url = _site_url()
                send_inline(
                    chat_id,
                    f'✅ *Бот @{bot.username} настроен!*\n\n'
                    f'📚 Раздел: *{article.title}*\n\n'
                    f'Пользователи могут писать @{bot.username} — он отвечает только по этому разделу.',
                    [[{'text': f'📖 {article.title[:35]}', 'url': f'{site_url}/kb/{article.slug}/'}]],
                    token=token,
                )
            except Exception as e:
                logger.error('pick_article for bot error: %s', e)
                send_message(chat_id, 'Ошибка. Попробуйте заново.', token=token)
        else:
            send_message(chat_id, 'Сессия устарела. Откройте /menu заново.', token=token)

    # ── Сменить страницу кастомного бота ──
    elif data.startswith('bot_change_page:'):
        bot_pk = int(data.split(':', 1)[1])
        bot_obj = CustomBot.objects.filter(pk=bot_pk, owner=user, is_active=True).first()
        if not bot_obj:
            send_message(chat_id, 'Бот не найден.', token=token)
            return
        setup = BotSetupState.get_state(chat_id)
        setup.set('pick_section_for_bot', pending_bot_pk=bot_pk)
        _send_article_picker(chat_id, user, purpose='bot', token=token)

    # ── Изменить модель OpenAI ──
    elif data.startswith('bot_model:'):
        from recordings.models import SystemConfig
        current_model = SystemConfig.get('openai_model', 'gpt-4o-mini')
        setup = BotSetupState.get_state(chat_id)
        setup.set('await_model_input', pending_token='', pending_bot_pk=None)
        send_message(
            chat_id,
            f'🧠 *Выбор модели OpenAI*\n\n'
            f'Текущая модель: `{current_model}`\n\n'
            f'Введите название модели:\n'
            f'• `gpt-4o-mini` — быстрая и дешёвая\n'
            f'• `gpt-4o` — умнее, дороже\n'
            f'• `gpt-4.1-mini` — новейшая дешёвая\n'
            f'• `gpt-4.1` — новейшая умная',
            token=token,
        )

    # ── Навигация по дереву вики ──
    elif data.startswith('wiki_children:'):
        parent_pk = int(data.split(':', 1)[1])
        _send_article_picker(chat_id, user, purpose='section', parent_pk=parent_pk, token=token)

    elif data == 'wiki_back:root':
        _send_article_picker(chat_id, user, purpose='section', token=token)


def _send_article_picker(chat_id, user, purpose='section', parent_pk=None, token=None):
    """Дерево статей вики кнопками. purpose: 'section' | 'bot'."""
    from wiki_kb.models import WikiArticle

    if parent_pk:
        parent = WikiArticle.objects.filter(pk=parent_pk, is_deleted=False).first()
        articles = WikiArticle.objects.filter(
            space=user.space, is_deleted=False, parent_id=parent_pk,
        ).order_by('order', 'title')[:20]
        header = f'📂 *{parent.title}* → выберите подраздел:'
    else:
        parent = None
        articles = WikiArticle.objects.filter(
            space=user.space, is_deleted=False, parent__isnull=True,
        ).order_by('order', 'title')[:20]
        header = '📚 Выберите раздел вики:'

    if not articles:
        send_message(chat_id, 'В этом разделе нет подстатей. Пространство вики пусто или добавьте статьи на сайте.', token=token)
        return

    buttons = []
    for a in articles:
        has_children = WikiArticle.objects.filter(parent=a, is_deleted=False).exists()
        row = [{'text': f'✅ {a.title}', 'callback_data': f'pick_article:{a.pk}'}]
        if has_children:
            row.append({'text': '▶', 'callback_data': f'wiki_children:{a.pk}'})
        buttons.append(row)

    if parent_pk:
        buttons.append([{'text': '⬆ Наверх', 'callback_data': 'wiki_back:root'}])

    send_inline(chat_id, header, buttons, token=token)


# ── Auth flows ────────────────────────────────────────────────────────────────

def _do_verify(chat_id, code, OrgRegistration, SiteUser, Space, generate_password, token=None):
    if OrgRegistration.objects.filter(tg_chat_id=chat_id, status=OrgRegistration.STATUS_VERIFIED).exists():
        send_message(chat_id, 'Эта учётная запись Telegram уже связана с организацией. Используйте /password для получения пароля.', token=token)
        return

    try:
        reg = OrgRegistration.objects.get(verify_code=code, status=OrgRegistration.STATUS_PENDING)
    except OrgRegistration.DoesNotExist:
        send_message(chat_id, 'Код не найден или уже использован.', token=token)
        return

    email = reg.email.lower()
    if SiteUser.objects.filter(email__iexact=email).exists():
        send_message(chat_id, 'Пользователь с таким email уже зарегистрирован.', token=token)
        return

    # Если задано целевое пространство — входим в него, не создаём новое
    if reg.target_space_id:
        space = reg.target_space
        join_existing = True
    else:
        slug = make_unique_space_slug(reg.org_name)
        space = Space.objects.create(name=reg.org_name, slug=slug)
        join_existing = False

    pwd = generate_password()
    user = SiteUser.objects.create(email=email, space=space, free_left=None if join_existing else 5)
    user.set_password(pwd)
    user.save(update_fields=['password'])

    reg.status = OrgRegistration.STATUS_VERIFIED
    reg.tg_chat_id = chat_id
    reg.space = space
    reg.save(update_fields=['status', 'tg_chat_id', 'space'])

    import datetime as _dt
    from django.utils import timezone as _tz
    from recordings.models import MagicLoginToken
    magic = MagicLoginToken.objects.create(user=user, expires_at=_tz.now() + _dt.timedelta(days=7))
    magic_url = f'{_site_url()}/magic-login/{magic.token}/'

    send_message(
        chat_id,
        f'*Организация {reg.org_name} зарегистрирована!*\n\n'
        f'Email: `{email}`\nПароль: `{pwd}`\n\n'
        f'*Войти одним кликом:*\n{magic_url}\n\n_(ссылка действует 7 дней)_\n\nНовый пароль: /password {email}',
        token=token,
    )


def _do_bp_tg_verify(chat_id, code, token=None):
    from recordings.models import SiteUser
    from django.utils import timezone

    try:
        user = SiteUser.objects.get(tg_verify_code=code)
    except SiteUser.DoesNotExist:
        send_message(chat_id, 'Код не найден или уже использован.', token=token)
        return

    if user.tg_verify_expires and timezone.now() > user.tg_verify_expires:
        send_message(chat_id, 'Срок действия кода истёк. Запросите новый код на сайте.', token=token)
        return

    if user.tg_verified:
        send_message(chat_id, 'Аккаунт уже подтверждён. Вернитесь на сайт и задайте пароль.', token=token)
        return

    user.tg_verified = True
    user.tg_chat_id = chat_id
    user.save(update_fields=['tg_verified', 'tg_chat_id'])
    send_message(chat_id, f'*Аккаунт подтверждён!*\n\nEmail: `{user.email}`\n\nВернитесь на сайт и задайте пароль для входа.', token=token)


# ── Main bot update handler ───────────────────────────────────────────────────

def handle_tg_update(data, token=None):
    """Обработать входящий Telegram webhook update (главный бот)."""
    from recordings.models import OrgRegistration, SiteUser, Space, BotSetupState, CustomBot
    from recordings.email_service import generate_password

    # Callback query (нажатие inline-кнопки)
    if 'callback_query' in data:
        _handle_callback(data['callback_query'], token=token)
        return

    message = data.get('message') or data.get('edited_message')
    if not message:
        return

    chat_id = message.get('chat', {}).get('id')
    text = (message.get('text') or '').strip()
    if not chat_id:
        return

    # ── Фото → OCR ──
    if not text and message.get('photo'):
        _handle_photo_ocr(chat_id, message, token=token)
        return

    # ── Аудио/видео → загрузка и транскрибация ──
    if not text and _get_media_info(message)[0]:
        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, '⚠️ Для загрузки файлов нужно авторизоваться.', token=token)
            return
        _handle_media_upload(chat_id, message, user, token=token)
        return

    if not text:
        return

    parts = text.split()
    command = parts[0].lower().split('@')[0]

    # Проверяем состояние setup-wizard
    setup = BotSetupState.get_state(chat_id)

    # ── Ввод названия модели OpenAI ──
    if setup.state == 'await_model_input' and not command.startswith('/'):
        from recordings.models import SystemConfig
        model_name = text.strip()
        SystemConfig.set('openai_model', model_name, description='OpenAI model for bot agent')
        setup.reset()
        send_message(chat_id, f'✅ Модель изменена на `{model_name}`.\n\nВсе боты теперь используют эту модель.', token=token)
        return

    # ── Ввод пароля ──
    if setup.state == 'await_password' and not command.startswith('/'):
        if text.strip() != _ADMIN_PASSWORD:
            send_message(chat_id, '❌ Неверный пароль.', token=token)
            setup.reset()
            return
        action = setup.pending_token  # мы использовали pending_token для хранения действия
        if action == 'new_bot':
            setup.set(BotSetupState.STATE_AWAIT_TOKEN, pending_token='')
            send_message(
                chat_id,
                '🤖 *Создание бота*\n\n'
                '1. Откройте @BotFather\n'
                '2. Создайте бота командой `/newbot`\n'
                '3. Скопируйте токен и отправьте его сюда\n\n'
                '_Ожидаю токен..._',
                token=token,
            )
        return

    if setup.state == BotSetupState.STATE_AWAIT_TOKEN and not command.startswith('/'):
        # Пользователь прислал токен бота
        bot_token = text.strip()
        info = get_bot_info(token=bot_token)
        if not info or not info.get('username'):
            send_message(chat_id, '❌ Токен не подходит. Проверьте и отправьте ещё раз.', token=token)
            return

        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if not user or not user.space:
            send_message(chat_id, 'Сначала авторизуйтесь.', token=token)
            setup.reset()
            return

        secret = _bot_webhook_secret(bot_token)
        bot_obj, created = CustomBot.objects.update_or_create(
            token=bot_token,
            defaults={
                'owner': user,
                'space': user.space,
                'name': info.get('first_name', ''),
                'username': info.get('username', ''),
                'webhook_secret': secret,
                'is_active': True,
            },
        )
        setup.set(BotSetupState.STATE_PICK_ARTICLE, pending_bot_pk=bot_obj.pk)
        _send_article_picker(chat_id, user, bot_obj.pk, token=token)
        return

    if command in ('/start', '📌 меню') or text == '📌 Меню':
        if command == '/start' and len(parts) >= 2:
            code = parts[1].strip()
            if code.startswith('BP'):
                _do_bp_tg_verify(chat_id, code, token=token)
            else:
                _do_verify(chat_id, code, OrgRegistration, SiteUser, Space, generate_password, token=token)
            return
        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if user and user.space:
            # Показываем возможности только на /start (не на кнопку «Меню»)
            if command == '/start':
                send_message(chat_id, _MAIN_BOT_ABOUT, token=token)
            _send_main_menu(chat_id, user, token=token, attach_keyboard=True)
        else:
            _attach_persistent_keyboard(chat_id, token=token)
            send_message(
                chat_id,
                '👋 *Привет!* Я бот платформы BusinessPad.\n\n'
                + _MAIN_BOT_ABOUT +
                '\n\n_Чтобы начать — зарегистрируйте организацию или подтвердите код._\n\n'
                'Команды:\n- /verify КОД — подтвердить регистрацию\n- /password EMAIL — получить новый пароль',
                token=token,
            )

    elif command == '/menu':
        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if user and user.space:
            _send_main_menu(chat_id, user, token=token)
        else:
            send_message(chat_id, 'Сначала авторизуйтесь (/verify КОД).', token=token)

    elif command == '/clear':
        from recordings.models import BotChatHistory
        BotChatHistory.clear(chat_id, bot_id=None)
        send_message(chat_id, '🗑 Контекст разговора сброшен.', token=token)

    elif command == '/verify':
        if len(parts) < 2:
            send_message(chat_id, 'Укажите код: /verify ВАШ\\_КОД', token=token)
            return
        _do_verify(chat_id, parts[1].strip(), OrgRegistration, SiteUser, Space, generate_password, token=token)

    elif command == '/password':
        if len(parts) >= 2:
            email = parts[1].strip().lower()
        else:
            reg = OrgRegistration.objects.filter(
                tg_chat_id=chat_id, status=OrgRegistration.STATUS_VERIFIED,
            ).first()
            if reg:
                email = reg.email.lower()
            else:
                send_message(chat_id, 'Укажите email: /password ваш@email.com', token=token)
                return

        try:
            user = SiteUser.objects.get(email__iexact=email)
        except SiteUser.DoesNotExist:
            send_message(chat_id, f'Пользователь {email} не найден.', token=token)
            return

        from recordings.email_service import generate_password as _gp
        pwd = _gp()
        user.set_password(pwd)
        user.save(update_fields=['password'])
        send_message(chat_id, f'Новый пароль для {email}:\n`{pwd}`', token=token)

    else:
        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if user and user.space:
            # Учитываем настроенный раздел вики
            section = _get_user_wiki_section(chat_id)
            article_ids = None
            if section:
                article_ids = [section.pk] + section.get_all_descendants_ids()
            _handle_wiki_rag(chat_id, text, user, token=token, article_ids=article_ids)
        else:
            send_message(
                chat_id,
                'Команды:\n- /verify КОД — подтвердить регистрацию\n- /password EMAIL — получить новый пароль',
                token=token,
            )


# ── Custom bot update handler ─────────────────────────────────────────────────

def handle_custom_bot_update(data, custom_bot):
    """Обработать update кастомного бота (привязан к разделу вики)."""
    from recordings.models import SiteUser

    token = custom_bot.token

    if 'callback_query' in data:
        answer_callback(data['callback_query']['id'], token=token)
        return

    message = data.get('message') or data.get('edited_message')
    if not message:
        return

    chat_id = message.get('chat', {}).get('id')
    text = (message.get('text') or '').strip()
    if not chat_id:
        return

    if not text and message.get('photo'):
        _handle_photo_ocr(chat_id, message, token=token)
        return

    # ── Аудио/видео → загрузка и транскрибация ──
    if not text and _get_media_info(message)[0]:
        user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, '⚠️ Для загрузки файлов нужно авторизоваться.', token=token)
            return
        _handle_media_upload(chat_id, message, user, token=custom_bot.token)
        return

    if not text:
        return

    parts = text.split()
    command = parts[0].lower().split('@')[0]

    if command == '/start':
        article = custom_bot.root_article
        section_name = article.title if article else 'базе знаний'
        wiki_link = f'{_site_url()}/kb/{article.slug}/' if article else f'{_site_url()}/kb/'
        about = (
            f'👋 *Привет!* Я AI-агент по разделу *«{section_name}»*.\n\n'
            f'*Что я умею:*\n'
            f'• 💬 Отвечать на вопросы по разделу *«{section_name}»* из базы знаний\n'
            f'• 📸 Распознавать текст с фото (OCR) и сохранять в вики\n'
            f'• 🎙 Принимать аудио и видео — ставить в очередь транскрибации\n'
            f'• 🧠 Запоминать контекст разговора (сбросить: /clear)\n\n'
            f'_Просто напишите вопрос или пришлите фото / аудио / видео._'
        )
        buttons = []
        if article:
            buttons.append([{'text': f'📖 Открыть раздел «{section_name[:35]}»', 'url': wiki_link}])
        send_inline(chat_id, about, buttons, token=token)
        return

    if command == '/clear':
        from recordings.models import BotChatHistory
        BotChatHistory.clear(chat_id, bot_id=custom_bot.pk)
        send_message(chat_id, '🗑 Контекст разговора сброшен.', token=token)
        return

    # Любой текст → RAG по поддереву раздела
    user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
    _handle_wiki_rag(chat_id, text, user=None, token=token, custom_bot=custom_bot)


# ── Активные встречи ──────────────────────────────────────────────────────────

def _send_active_meetings(chat_id, user, token=None):
    """Показать активные встречи — получаем данные через API сайта."""
    from django.conf import settings as _s
    site_url = _site_url()
    master_key = getattr(_s, 'MASTER_API_KEY', '')

    try:
        resp = req.get(
            f'{site_url}/api/active-meetings/',
            params={'chat_id': chat_id},
            headers={'X-Agent-Key': master_key},
            timeout=15,
        )
        resp.raise_for_status()
        rooms = resp.json().get('meetings', [])
    except Exception as e:
        logger.warning('Active meetings API failed: %s', e)
        send_message(chat_id, '📹 Не удалось получить список встреч.', token=token)
        return

    if not rooms:
        send_message(chat_id, '📹 Активных встреч сейчас нет.', token=token)
        return

    lines = []
    for rm in rooms:
        cnt_str = f' · {rm["participants"]} уч.' if rm.get('participants') else ''
        lines.append(f'• *{rm["title"]}*{cnt_str}\n  {rm["url"]}')

    buttons = [[{'text': f'▶ {rm["title"]}', 'url': rm['url']}] for rm in rooms[:5]]
    send_inline(chat_id, '📹 *Встречи прямо сейчас:*\n\n' + '\n\n'.join(lines), buttons, token=token)


# ── OCR из фото ───────────────────────────────────────────────────────────────

def _handle_photo_ocr(chat_id, message, token=None):
    """Принять фото из Telegram, отправить в OCR, прислать ссылку на результат."""
    from recordings.models import SiteUser
    user = SiteUser.objects.filter(tg_chat_id=chat_id).first()
    if not user:
        send_message(chat_id, '⚠️ Для распознавания текста с фото нужно авторизоваться.', token=token)
        return

    # Берём самую большую версию фото
    photos = message.get('photo', [])
    if not photos:
        return
    file_id = photos[-1]['file_id']

    send_message(chat_id, '🔍 Получил фото, отправляю на распознавание...', token=token)

    try:
        # Получаем file_path от Telegram
        bot_token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        file_info = _api('getFile', token=token, file_id=file_id)
        if not file_info:
            send_message(chat_id, '❌ Не удалось получить файл от Telegram.', token=token)
            return
        file_path = file_info['file_path']
        file_url = f'https://api.telegram.org/file/bot{bot_token}/{file_path}'

        # Скачиваем фото
        import io
        r = req.get(file_url, timeout=30)
        r.raise_for_status()
        file_bytes = r.content
        filename = file_path.split('/')[-1]

        # Отправляем в OCR API
        ocr_url = getattr(settings, 'OCR_API_URL', '').rstrip('/')
        if not ocr_url:
            send_message(chat_id, '❌ OCR-сервис не настроен.', token=token)
            return

        files = {'file': (filename, io.BytesIO(file_bytes), 'image/jpeg')}
        headers = {}
        ocr_key = getattr(settings, 'OCR_API_KEY', '')
        if ocr_key:
            headers['Authorization'] = f'Bearer {ocr_key}'

        ocr_resp = req.post(ocr_url, files=files, headers=headers, timeout=60)
        ocr_resp.raise_for_status()
        result = ocr_resp.json()

        text_result = result.get('markdown') or result.get('text') or ''
        if not text_result:
            send_message(chat_id, '⚠️ OCR не нашёл текст на изображении.', token=token)
            return

        # Сохраняем результат в OcrJob
        from recordings.models import OcrJob, BotChatHistory, CustomBot
        ocr_job = OcrJob.objects.create(
            original_filename=filename,
            status='done',
            result_markdown=text_result,
        )

        # Сохраняем в историю чата
        bot_id = None
        if token:
            cb = CustomBot.objects.filter(token=token, is_active=True).first()
            if cb:
                bot_id = cb.pk
        BotChatHistory.add(
            chat_id, bot_id, 'ocr',
            f'📷 [{filename}] — OCR распознан',
            ocr_job=ocr_job,
        )

        site_url = _site_url()
        ocr_url = f'{site_url}/ocr/{ocr_job.pk}/'  # → ocr_job_detail
        send_inline(
            chat_id,
            f'✅ *Фото распознано!*',
            [[{'text': '📄 Открыть результат на сайте', 'url': ocr_url}]],
            token=token,
        )

    except Exception as e:
        logger.error('Telegram OCR failed for chat %s: %s', chat_id, e)
        send_message(chat_id, '❌ Ошибка при распознавании. Попробуйте ещё раз.', token=token)


# ── Загрузка аудио/видео из Telegram ─────────────────────────────────────────

def _get_media_info(message):
    """Вернуть (file_id, mime_type, original_filename) для аудио/видео/голоса/документа."""
    if message.get('audio'):
        m = message['audio']
        fname = m.get('file_name') or 'audio.mp3'
        return m['file_id'], m.get('mime_type', 'audio/mpeg'), fname
    if message.get('voice'):
        m = message['voice']
        return m['file_id'], m.get('mime_type', 'audio/ogg'), 'voice.ogg'
    if message.get('video'):
        m = message['video']
        return m['file_id'], m.get('mime_type', 'video/mp4'), 'video.mp4'
    if message.get('video_note'):
        m = message['video_note']
        return m['file_id'], 'video/mp4', 'video_note.mp4'
    if message.get('document'):
        m = message['document']
        mime = m.get('mime_type', '')
        fname = m.get('file_name') or 'file'
        if mime.startswith('audio/') or mime.startswith('video/'):
            return m['file_id'], mime, fname
    return None, None, None


def _handle_media_upload(chat_id, message, user, token=None):
    """Скачать аудио/видео из Telegram, загрузить на сайт и поставить в очередь транскрибации."""
    import io
    import os
    import tempfile
    import uuid
    import subprocess
    from recordings.models import Recording
    from recordings.s3_client import upload_file_to_s3
    from recordings.queue_services import enqueue_transcribe

    file_id, mime_type, orig_filename = _get_media_info(message)
    if not file_id:
        return

    # Free tier check
    if user and user.free_left is not None:
        if user.free_left <= 0:
            send_message(chat_id, '⚠️ Лимит бесплатных транскрибаций исчерпан.', token=token)
            return

    send_message(chat_id, '⏳ Получаю файл...', token=token)

    try:
        bot_token = token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')

        # Проверяем размер файла (Telegram ограничивает 20MB для ботов без расширений)
        file_info = _api('getFile', token=token, file_id=file_id)
        if not file_info:
            send_message(chat_id, '❌ Не удалось получить файл от Telegram.', token=token)
            return

        file_path = file_info['file_path']
        file_url = f'https://api.telegram.org/file/bot{bot_token}/{file_path}'

        send_message(chat_id, '📤 Скачиваю и конвертирую...', token=token)

        r = req.get(file_url, timeout=120, stream=True)
        r.raise_for_status()

        ext = os.path.splitext(orig_filename)[1].lower() or '.bin'
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            for chunk in r.iter_content(chunk_size=65536):
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            # Конвертируем в MP3 если нужно
            if ext != '.mp3':
                mp3_path = tmp_path + '.mp3'
                try:
                    subprocess.run(
                        ['ffmpeg', '-y', '-i', tmp_path, '-acodec', 'libmp3lame', '-q:a', '2', mp3_path],
                        check=True, capture_output=True, timeout=300,
                    )
                    os.remove(tmp_path)
                    tmp_path = mp3_path
                    safe_name = os.path.splitext(orig_filename)[0] + '.mp3'
                except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as conv_err:
                    logger.warning('ffmpeg conversion failed: %s', conv_err)
                    # Загружаем как есть, попробуем транскрибировать
                    safe_name = orig_filename
            else:
                safe_name = orig_filename

            prefix = (getattr(settings, 'S3_PREFIX', None) or '').strip()
            if prefix and not prefix.endswith('/'):
                prefix += '/'

            safe_name = safe_name.replace('/', '_').replace('\\', '_')[-200:]
            s3_key = f"{prefix}{uuid.uuid4().hex}_{safe_name}"

            upload_file_to_s3(tmp_path, s3_key, content_type='audio/mpeg')
            size = os.path.getsize(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        rec = Recording.objects.create(
            s3_key=s3_key,
            filename=safe_name,
            size_bytes=size,
            status=Recording.Status.STABLE,
            space=user.space if user else None,
        )

        if user and user.free_left is not None:
            user.free_left -= 1
            user.save(update_fields=['free_left'])

        enqueue_transcribe(rec, priority=1)

        # Сохраняем в историю чата со ссылкой на запись
        from recordings.models import BotChatHistory, CustomBot
        bot_id = None
        if token:
            cb = CustomBot.objects.filter(token=token, is_active=True).first()
            if cb:
                bot_id = cb.pk
        BotChatHistory.add(
            chat_id, bot_id, 'audio',
            f'🎙 [{safe_name}] — загружено, ожидает транскрибации',
            recording=rec,
        )

        site_url = _site_url()
        rec_url = f'{site_url}/r/{rec.pk}/'
        send_inline(
            chat_id,
            f'✅ *Файл загружен и поставлен в очередь!*\n\nТранскрибация начнётся в ближайшее время.',
            [[{'text': '📄 Открыть запись на сайте', 'url': rec_url}]],
            token=token,
        )

    except Exception as e:
        logger.error('Telegram media upload failed for chat %s: %s', chat_id, e)
        send_message(chat_id, '❌ Ошибка при загрузке файла. Попробуйте ещё раз.', token=token)


# ── RAG ───────────────────────────────────────────────────────────────────────

def _handle_wiki_rag(chat_id, query: str, user, token=None, custom_bot=None, article_ids=None):
    """AI-агент: ReAct loop с памятью и инструментами."""
    from recordings.bot_agent import run_agent

    space = None
    bot_id = None
    if custom_bot:
        space = custom_bot.space
        article_ids = custom_bot.get_article_ids()
        bot_id = custom_bot.pk
    elif user:
        space = user.space

    try:
        answer, sources = run_agent(
            chat_id=chat_id,
            user_message=query,
            space=space,
            bot_id=bot_id,
            custom_bot=custom_bot,
            article_ids=article_ids,
        )
    except Exception as e:
        logger.error('Agent failed chat=%s: %s', chat_id, e, exc_info=True)
        answer = '⚠️ Ошибка агента. Попробуйте ещё раз или /clear чтобы сбросить контекст.'
        sources = []

    if not answer:
        answer = '📭 Не удалось получить ответ.'

    # Кнопки-ссылки на источники
    site_url = _site_url()
    buttons = []
    seen = set()
    for art in sources:
        if art.slug not in seen and len(buttons) < 5:
            seen.add(art.slug)
            buttons.append([{'text': f'📖 {art.title[:40]}', 'url': f'{site_url}/kb/{art.slug}/'}])

    if buttons:
        send_inline(chat_id, answer, buttons, token=token)
    else:
        send_message(chat_id, answer, token=token)
