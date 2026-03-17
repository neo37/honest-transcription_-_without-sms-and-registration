import re
import json
import time
import logging
import threading

logger = logging.getLogger(__name__)
from datetime import date
from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.utils.text import slugify
from .models import Recording, PollLog, Comment, OcrJob, AccessLog, ShareToken, TagDefinition, Space, SiteUser, OrgRegistration, MagicLoginToken, MascotLog, SystemConfig, MeetingRoom, BotChatHistory, CustomBot, DayShareLink, MeetingAttendee, RecurringBusyTime, DirectMessage, BPChatTopic, BPChatMessage
from .auth_backend import site_login_required, get_current_user
from . import services
from .s3_client import get_presigned_download_url, upload_file_to_s3


# ─── Вспомогательные функции для логирования ───────────────────────────────

def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def _parse_os(user_agent):
    ua = user_agent or ''
    for pattern, name in [
        (r'Windows NT 1[01]', 'Windows 10/11'),
        (r'Windows NT 6\.3', 'Windows 8.1'),
        (r'Windows NT 6\.2', 'Windows 8'),
        (r'Windows NT 6\.1', 'Windows 7'),
        (r'Windows', 'Windows'),
        (r'iPhone', 'iOS (iPhone)'),
        (r'iPad', 'iOS (iPad)'),
        (r'Android', 'Android'),
        (r'Mac OS X', 'macOS'),
        (r'Linux', 'Linux'),
    ]:
        if re.search(pattern, ua):
            return name
    return ''


def _log_access(request, event, recording=None):
    try:
        ua = request.META.get('HTTP_USER_AGENT', '')
        user = get_current_user(request)
        username = user.email if user else request.session.get('site_username', '')
        tg_chat_id = user.tg_chat_id if user else None
        tg_username = user.tg_username if user else ''
        AccessLog.objects.create(
            username=username,
            ip=_get_client_ip(request),
            user_agent=ua[:500],
            os_name=_parse_os(ua),
            screen=request.session.get('screen_resolution', ''),
            tg_chat_id=tg_chat_id,
            tg_username=tg_username,
            event=event,
            recording=recording,
        )
    except Exception:
        pass


@site_login_required
def api_speaker_profiles(request):
    """Список голосовых профилей спикеров в пространстве."""
    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'profiles': []})
    profiles = list(user.space.speaker_profiles.values('name', 'sample_count').order_by('name'))
    return JsonResponse({'profiles': profiles})


@site_login_required
def api_transcribing_progress(request):
    """Список активных транскрибаций с прогрессом + очередь ожидания."""
    from .models import TranscribeQueue
    active = list(Recording.objects.filter(status=Recording.Status.TRANSCRIBING).values(
        'id', 'filename', 'ai_title', 'transcription_progress', 'transcription_stage'
    ))
    queue_rows = list(
        TranscribeQueue.objects
        .select_related('recording')
        .order_by('-priority', 'created_at')
        .values('recording_id', 'recording__filename', 'recording__ai_title')
    )
    queued = [
        {
            'id': row['recording_id'],
            'filename': row['recording__filename'],
            'ai_title': row['recording__ai_title'],
            'position': idx + 1,
        }
        for idx, row in enumerate(queue_rows)
    ]
    return JsonResponse({'active': active, 'queued': queued})


@csrf_exempt
@require_http_methods(['POST'])
def log_screen(request):
    """Сохранить разрешение экрана в сессии (AJAX, без CSRF)."""
    import json
    try:
        data = json.loads(request.body)
        screen = str(data.get('screen', ''))[:20]
        if screen and 'x' in screen:
            request.session['screen_resolution'] = screen
    except Exception:
        pass
    return JsonResponse({'ok': True})


@never_cache
@ensure_csrf_cookie
@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.session.get('user_id'):
        next_url = request.GET.get('next', '')
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect(reverse('recordings:index'))
    error = None
    registered = request.GET.get('registered') == '1'
    if request.method == 'POST':
        from .user_service import get_or_create_user, _is_bp_email
        email = (request.POST.get('email') or '').strip().lower()
        password = (request.POST.get('password') or '').strip()
        password2 = (request.POST.get('password2') or '').strip()
        if not email:
            error = 'Введите email.'
        elif not _is_bp_email(email):
            error = 'Эта страница только для сотрудников BusinessPad (@core.business-pad.com).'
        else:
            user, _ = get_or_create_user(email)
            if user.first_login_at is None:
                # Первый вход: требуется TG верификация и установка пароля
                if not user.tg_verified:
                    error = 'Сначала подтвердите аккаунт через Telegram.'
                elif not password:
                    error = 'Придумайте пароль.'
                elif len(password) < 6:
                    error = 'Пароль должен быть не короче 6 символов.'
                elif password != password2:
                    error = 'Пароли не совпадают.'
                else:
                    from django.utils import timezone as tz
                    user.set_password(password)
                    user.first_login_at = tz.now()
                    user.save(update_fields=['password', 'first_login_at'])
                    request.session['user_id'] = user.pk
                    request.session.set_expiry(60 * 60 * 24 * 7)
                    request.session.modified = True
                    _log_access(request, AccessLog.EVENT_LOGIN)
                    next_url = request.POST.get('next') or request.GET.get('next') or ''
                    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                        return redirect(next_url)
                    return redirect(reverse('recordings:index'))
            elif not password:
                error = 'Введите пароль.'
            elif not user.check_password(password):
                error = 'Неверный пароль.'
            else:
                request.session['user_id'] = user.pk
                request.session.set_expiry(60 * 60 * 24 * 7)
                request.session.modified = True
                _log_access(request, AccessLog.EVENT_LOGIN)
                next_url = request.POST.get('next') or request.GET.get('next') or ''
                if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                    return redirect(next_url)
                return redirect(reverse('recordings:index'))
    bp_emails = sorted(settings.BP_EMAILS)
    next_url = request.GET.get('next', '')
    return render(request, 'recordings/login.html', {
        'error': error,
        'registered': registered,
        'bp_emails': bp_emails,
        'next_url': next_url,
    })


@require_http_methods(['POST'])
def api_request_tg_verify(request):
    """Генерировать код TG верификации для BP пользователя (первый вход)."""
    import secrets
    import datetime
    from .user_service import _is_bp_email, get_or_create_user
    email = (request.POST.get('email') or '').strip().lower()
    if not email or not _is_bp_email(email):
        return JsonResponse({'ok': False, 'reason': 'not_bp'})
    user, _ = get_or_create_user(email)
    if user.first_login_at is not None:
        return JsonResponse({'ok': False, 'reason': 'already_registered'})
    code = 'BP' + secrets.token_hex(4).upper()
    user.tg_verify_code = code
    user.tg_verify_expires = timezone.now() + datetime.timedelta(minutes=15)
    user.tg_verified = False
    user.save(update_fields=['tg_verify_code', 'tg_verify_expires', 'tg_verified'])
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '')
    bot_link = f'https://t.me/{bot_username}?start={code}' if bot_username else ''
    return JsonResponse({'ok': True, 'code': code, 'bot_link': bot_link})


@site_login_required
@require_http_methods(['POST'])
def api_ask_transcription(request):
    """AJAX: задать вопрос по транскрипции → LLM → создать статьи в KB.

    Структура статей:
      [родитель] Суть встречи: <название> — резюме встречи (создаётся 1 раз, переиспользуется)
      [дочерняя] Вопрос: <вопрос>          — Q&A по конкретному вопросу
    """
    import json as _json
    from recordings.services import call_llm_api
    from wiki_kb.models import WikiArticle
    from wiki_kb.views import _unique_slug
    from django.urls import reverse as _reverse

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    rec_id = body.get('recording_id')
    question = (body.get('question') or '').strip()
    if not rec_id or not question:
        return JsonResponse({'ok': False, 'error': 'recording_id and question required'}, status=400)

    user = get_current_user(request)
    try:
        rec = Recording.objects.get(pk=rec_id)
    except Recording.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Recording not found'}, status=404)

    transcription = (rec.transcription or '').strip()
    if not transcription:
        return JsonResponse({'ok': False, 'error': 'Транскрипция недоступна'}, status=400)

    rec_title = rec.ai_title or rec.filename

    prompt = (
        f"Ты — ассистент по анализу встреч. Ниже дана транскрипция встречи.\n\n"
        f"Сделай два блока:\n\n"
        f"**БЛОК 1 — СУТЬ ВСТРЕЧИ**: Напиши структурированное резюме транскрипции: "
        f"ключевые темы, принятые решения, важные выводы, договорённости. "
        f"Используй Markdown-списки и подзаголовки.\n\n"
        f"**БЛОК 2 — ОТВЕТ НА ВОПРОС**: Дай развёрнутый и точный ответ на вопрос пользователя, "
        f"опираясь на содержание транскрипции.\n\n"
        f"Формат ответа строго такой:\n"
        f"## Суть встречи\n"
        f"[твой анализ]\n\n"
        f"## Ответ на вопрос\n"
        f"[твой ответ]\n\n"
        f"---\n\n"
        f"ТРАНСКРИПЦИЯ:\n{transcription[:12000]}\n\n"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n{question}"
    )

    llm_result = call_llm_api(
        prompt=prompt,
        session_id=f'ask-{rec.pk}',
        log_id=f'ask-rec-{rec.pk}',
        timeout=120,
    )

    if not llm_result:
        return JsonResponse({'ok': False, 'error': 'LLM не вернул ответ. Попробуйте позже.'}, status=502)

    # Разбиваем ответ LLM на два блока
    import re as _re
    summary_text = ''
    answer_text = ''
    m = _re.split(r'##\s*Ответ на вопрос', llm_result, maxsplit=1, flags=_re.IGNORECASE)
    if len(m) == 2:
        summary_part = _re.sub(r'^##\s*Суть встречи\s*', '', m[0], flags=_re.IGNORECASE).strip()
        summary_text = summary_part
        answer_text = m[1].strip()
    else:
        # LLM не соблюл формат — всё в ответ, суть пустая
        answer_text = llm_result.strip()

    # ── Родительская статья: суть встречи ──────────────────────────────────
    # Ищем уже существующую для этой записи
    parent_title = f"Суть встречи: {rec_title[:80]}"
    parent_article = (
        WikiArticle.objects
        .filter(recordings=rec, space=user.space, parent=None, is_deleted=False)
        .filter(title__startswith='Суть встречи:')
        .first()
    )

    if parent_article is None:
        parent_content = (
            f"> Резюме встречи «{rec_title}», создано автоматически.\n\n"
            f"## Суть встречи\n\n"
            f"{summary_text if summary_text else '_Резюме будет сформировано при следующем вопросе._'}"
        )
        parent_slug = _unique_slug(parent_title)
        parent_article = WikiArticle.objects.create(
            title=parent_title,
            slug=parent_slug,
            content=parent_content,
            space=user.space,
            parent=None,
            created_by=user,
            updated_by=user,
        )
        parent_article.recordings.set([rec])
    elif summary_text and '## Суть встречи' not in parent_article.content:
        # Дополняем родителя резюме если его ещё не было
        parent_article.content += f"\n\n## Суть встречи\n\n{summary_text}"
        parent_article.updated_by = user
        parent_article.save(update_fields=['content', 'updated_by', 'updated_at'])

    # ── Дочерняя статья: вопрос + ответ ────────────────────────────────────
    child_title = f"Вопрос: {question[:80]}"
    child_content = (
        f"> Вопрос по встрече «{rec_title}»\n\n"
        f"## Вопрос\n\n"
        f"{question}\n\n"
        f"## Ответ\n\n"
        f"{answer_text}"
    )
    child_slug = _unique_slug(child_title)
    child_article = WikiArticle.objects.create(
        title=child_title,
        slug=child_slug,
        content=child_content,
        space=user.space,
        parent=parent_article,
        created_by=user,
        updated_by=user,
    )
    child_article.recordings.set([rec])

    article_url = _reverse('wiki_kb:article_detail', kwargs={'slug': child_article.slug})
    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
    full_article_url = f"{site_url}{article_url}"

    # Дублируем вопрос в комментарий к записи со ссылкой на статью в вики
    Comment.objects.create(
        recording=rec,
        text=f"Вопрос: {question}\n\nОтвет в базе знаний: {full_article_url}",
    )

    return JsonResponse({'ok': True, 'article_url': article_url, 'article_title': child_title})


@require_http_methods(['GET'])
def api_check_tg_verify(request):
    """Проверить статус TG верификации BP пользователя."""
    from .user_service import _is_bp_email
    email = (request.GET.get('email') or '').strip().lower()
    if not email or not _is_bp_email(email):
        return JsonResponse({'verified': False})
    try:
        user = SiteUser.objects.get(email__iexact=email)
        return JsonResponse({'verified': user.tg_verified})
    except SiteUser.DoesNotExist:
        return JsonResponse({'verified': False})


@require_http_methods(['GET'])
def api_check_email(request):
    """Проверить, является ли email BP-пользователем и был ли первый вход."""
    from .user_service import _is_bp_email
    email = (request.GET.get('email') or '').strip().lower()
    if not email or not _is_bp_email(email):
        return JsonResponse({'ok': False, 'reason': 'not_bp'})
    try:
        user = SiteUser.objects.get(email__iexact=email)
        return JsonResponse({'ok': True, 'first_login': user.first_login_at is None})
    except SiteUser.DoesNotExist:
        return JsonResponse({'ok': True, 'first_login': True})


@never_cache
@ensure_csrf_cookie
@require_http_methods(['GET', 'POST'])
def pilot_login(request):
    """Вход для участников пространств (не BP-пользователи)."""
    if request.session.get('user_id'):
        next_url = request.GET.get('next', '')
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect(reverse('recordings:index'))
    error = None
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        password = (request.POST.get('password') or '').strip()
        if not email or not password:
            error = 'Введите email и пароль.'
        else:
            try:
                user = SiteUser.objects.select_related('space').get(email__iexact=email)
                if user.check_password(password):
                    request.session['user_id'] = user.pk
                    request.session.set_expiry(60 * 60 * 24 * 7)
                    request.session.modified = True
                    _log_access(request, AccessLog.EVENT_LOGIN)
                    next_url = request.POST.get('next') or request.GET.get('next') or ''
                    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                        return redirect(next_url)
                    return redirect(reverse('recordings:index'))
                else:
                    error = 'Неверный пароль.'
            except SiteUser.DoesNotExist:
                error = 'Пользователь не найден. Обратитесь к администратору.'
    next_url = request.GET.get('next', '')
    return render(request, 'recordings/pilot_login.html', {'error': error, 'next_url': next_url})


# ─── smarty.rest login (landing page) ──────────────────────────────────────

SMARTY_SPACE_SLUG = 'spacecode-smarty'


@require_http_methods(['GET'])
def api_check_smarty_email(request):
    """Проверить, существует ли пользователь в пространстве spacecode-smarty."""
    email = (request.GET.get('email') or '').strip().lower()
    if not email:
        return JsonResponse({'ok': False, 'reason': 'no_email'})
    try:
        user = SiteUser.objects.get(email__iexact=email, space__slug=SMARTY_SPACE_SLUG)
        return JsonResponse({'ok': True, 'first_login': user.first_login_at is None})
    except SiteUser.DoesNotExist:
        return JsonResponse({'ok': False, 'reason': 'not_found'})


def smarty_register(request):
    """Регистрация для smarty.rest — создаёт OrgRegistration в пространстве spacecode-smarty."""
    import random, string
    from .telegram_service import get_bot_info

    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''
    if not bot_username:
        info = get_bot_info()
        bot_username = info.get('username', '')

    error = None
    success_code = None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        email = (request.POST.get('email') or '').strip().lower()
        if not name or not email:
            error = 'Заполните все поля.'
        elif SiteUser.objects.filter(email__iexact=email).exists():
            error = 'Пользователь с таким email уже зарегистрирован.'
        elif OrgRegistration.objects.filter(email__iexact=email, status='pending').exists():
            reg = OrgRegistration.objects.filter(email__iexact=email, status='pending').first()
            success_code = reg.verify_code
        else:
            try:
                target_space = Space.objects.get(slug=SMARTY_SPACE_SLUG)
            except Space.DoesNotExist:
                target_space = Space.objects.create(name='SpaceCode Smarty', slug=SMARTY_SPACE_SLUG)
            chars = string.ascii_uppercase + string.digits
            code = ''.join(random.choices(chars, k=8))
            while OrgRegistration.objects.filter(verify_code=code).exists():
                code = ''.join(random.choices(chars, k=8))
            OrgRegistration.objects.create(
                org_name=name, email=email, verify_code=code,
                target_space=target_space,
            )
            success_code = code

    return JsonResponse({
        'ok': success_code is not None,
        'code': success_code,
        'bot_username': bot_username,
        'error': error,
    })


@never_cache
@ensure_csrf_cookie
@require_http_methods(['GET', 'POST'])
def smarty_login(request):
    """Landing + login для домена smarty.rest."""
    if request.session.get('user_id'):
        next_url = request.GET.get('next', '')
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect(reverse('recordings:index'))
    error = None
    if request.method == 'POST':
        from django.utils import timezone as tz
        email    = (request.POST.get('email')     or '').strip().lower()
        password = (request.POST.get('password')  or '').strip()
        password2= (request.POST.get('password2') or '').strip()
        if not email or not password:
            error = 'Введите email и пароль.'
        else:
            try:
                user = SiteUser.objects.select_related('space').get(
                    email__iexact=email, space__slug=SMARTY_SPACE_SLUG,
                )
                if user.first_login_at is None:
                    # Первый вход — устанавливаем свой пароль
                    if len(password) < 6:
                        error = 'Пароль должен быть не короче 6 символов.'
                    elif password != password2:
                        error = 'Пароли не совпадают.'
                    else:
                        user.set_password(password)
                        user.first_login_at = tz.now()
                        user.save(update_fields=['password', 'first_login_at'])
                        request.session['user_id'] = user.pk
                        request.session.set_expiry(60 * 60 * 24 * 7)
                        request.session.modified = True
                        _log_access(request, AccessLog.EVENT_LOGIN)
                        next_url = request.POST.get('next') or request.GET.get('next') or ''
                        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                            return redirect(next_url)
                        return redirect(reverse('recordings:index'))
                elif user.check_password(password):
                    request.session['user_id'] = user.pk
                    request.session.set_expiry(60 * 60 * 24 * 7)
                    request.session.modified = True
                    _log_access(request, AccessLog.EVENT_LOGIN)
                    next_url = request.POST.get('next') or request.GET.get('next') or ''
                    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                        return redirect(next_url)
                    return redirect(reverse('recordings:index'))
                else:
                    error = 'Неверный пароль.'
            except SiteUser.DoesNotExist:
                error = 'Пользователь не найден. Зарегистрируйтесь через форму рядом.'
    next_url = request.GET.get('next', '')
    return render(request, 'recordings/smarty_landing.html', {'error': error, 'next_url': next_url})


@require_http_methods(['GET', 'POST'])
def pilot_integration(request):
    """Регистрация нового пространства (организации) на пилотной интеграции."""
    from .user_service import get_or_create_user, set_password_no_email
    error = None
    if request.method == 'POST':
        org_name = (request.POST.get('org_name') or '').strip()
        slug = (request.POST.get('slug') or '').strip().lower()
        emails_raw = request.POST.getlist('emails')
        emails = [e.strip().lower() for e in emails_raw if e.strip()]

        if not org_name:
            error = 'Введите название организации.'
        elif len(org_name) < 4:
            error = 'Название организации должно содержать не менее 4 символов.'
        elif not slug:
            error = 'Введите slug пространства.'
        elif not re.match(r'^[a-z0-9\-]+$', slug):
            error = 'Slug может содержать только латинские буквы, цифры и дефисы.'
        elif Space.objects.filter(slug=slug).exists():
            error = f'Пространство с slug «{slug}» уже существует.'
        elif not emails:
            error = 'Добавьте хотя бы один email участника.'
        else:
            space = Space.objects.create(name=org_name, slug=slug)
            pwd_list = []  # [(email, password), ...]
            for email in emails:
                try:
                    user, _ = get_or_create_user(email)
                    if user.space != space:
                        user.space = space
                        user.free_left = None
                        user.save(update_fields=['space', 'free_left'])
                    plain = set_password_no_email(user)
                    if plain:
                        pwd_list.append((email, plain))
                    else:
                        pwd_list.append((email, '(пароль уже выдан ранее)'))
                except Exception:
                    pass
            return render(request, 'recordings/pilot_integration.html', {
                'done': True,
                'space_name': org_name,
                'pwd_list': pwd_list,
            })

    return render(request, 'recordings/pilot_integration.html', {'error': error})


@site_login_required
def admin_passwords(request):
    """Список пользователей, которые ещё ни разу не входили (first_login_at is None).
    Позволяет администратору видеть и разослать пароли вручную.
    """
    current_user = get_current_user(request)
    # Показываем всех незалогиненных пользователей того же пространства (или все для BP)
    if current_user and current_user.space:
        qs = SiteUser.objects.filter(space=current_user.space, first_login_at__isnull=True)
    else:
        qs = SiteUser.objects.filter(first_login_at__isnull=True)
    pending = list(qs.order_by('email'))
    first_pwd = (settings.FIRST_LOGIN_PASSWORD or '').strip()
    return render(request, 'recordings/admin_passwords.html', {
        'pending': pending,
        'first_login_password': first_pwd,
        'current_user': current_user,
    })


@site_login_required
def description_view(request):
    """Страница с описанием приложения."""
    return render(request, 'recordings/description.html')


def manifest_view(request):
    """PWA manifest."""
    from django.http import JsonResponse
    from django.templatetags.static import static
    start_url = request.build_absolute_uri(reverse('recordings:index'))
    icon_url = request.build_absolute_uri(static('recordings/img/bp_logo.svg'))
    resp = JsonResponse({
        'name': 'Записи встреч',
        'short_name': 'Записи',
        'start_url': start_url,
        'display': 'standalone',
        'theme_color': '#0D2A7A',
        'background_color': '#FFFFFF',
        'icons': [
            {'src': icon_url, 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'},
        ],
    })
    resp['Content-Type'] = 'application/manifest+json; charset=utf-8'
    return resp


def service_worker_view(request):
    """Service worker для PWA: статику кэшируем, навигацию — network-first (свежий контент после деплоя)."""
    from django.http import HttpResponse
    start_url = request.build_absolute_uri(reverse('recordings:index'))
    static_base = request.build_absolute_uri('/static/')
    js = '''const CACHE = 'recordings-v2';
const STATIC = ''' + repr(static_base) + ''';
self.addEventListener('install', function(e) {
  e.waitUntil(self.skipWaiting());
});
self.addEventListener('activate', function(e) {
  e.waitUntil(caches.keys().then(function(keys) {
    return Promise.all(keys.filter(function(k) { return k !== CACHE; }).map(function(k) { return caches.delete(k); }));
  }).then(function() { return self.clients.claim(); }));
});
self.addEventListener('fetch', function(e) {
  var url = e.request.url;
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(function() { return caches.match(e.request); }));
    return;
  }
  if (url.startsWith(STATIC)) {
    e.respondWith(caches.match(e.request).then(function(r) { return r || fetch(e.request).then(function(res) {
      var clone = res.clone();
      caches.open(CACHE).then(function(cache) { cache.put(e.request, clone); });
      return res;
    }); }));
  }
});
'''
    r = HttpResponse(js, content_type='application/javascript')
    r['Cache-Control'] = 'no-store, max-age=0'
    return r


@require_http_methods(['POST'])
def logout_view(request):
    request.session.flush()
    return redirect(reverse('recordings:login'))


def _collect_speaker_names(current_user, base_qs):
    """Собрать уникальные имена спикеров из recording.speaker_names + SpeakerProfile."""
    names = set()
    # Из профилей пространства
    if current_user and current_user.space:
        for n in current_user.space.speaker_profiles.values_list('name', flat=True):
            names.add(n)
    # Из speaker_names всех записей пространства (значения JSON-dict)
    for rec in base_qs.exclude(speaker_names={}).values_list('speaker_names', flat=True):
        if isinstance(rec, dict):
            for v in rec.values():
                if v and isinstance(v, str) and v.strip():
                    names.add(v.strip())
    return sorted(names)


def _semantic_available():
    from django.db import connection
    if connection.vendor != 'postgresql':
        return False
    try:
        from .models import RecordingEmbedding
        return True
    except (ImportError, AttributeError):
        return False


@site_login_required
def index(request):
    from datetime import datetime
    from django.db.utils import OperationalError
    from .search import fulltext_search, semantic_search

    current_user = get_current_user(request)
    user_space_slug = current_user.space.slug if (current_user and current_user.space) else ''

    tab = (request.GET.get('tab') or 'date').strip()
    if tab not in ('date', 'ft', 'ext'):
        tab = 'date'

    ft_q = (request.GET.get('q') or '').strip() if tab == 'ft' else ''
    ext_q = (request.GET.get('q') or '').strip() if tab == 'ext' else ''
    search_comments = request.GET.get('search_comments') == '1'
    ext_limit = 25
    ext_min_score = 0.2
    if tab == 'ext':
        try:
            l = request.GET.get('ext_limit', '25')
            if l.isdigit():
                ext_limit = max(5, min(100, int(l)))
        except (ValueError, TypeError):
            pass
        try:
            s = request.GET.get('ext_min_score', '')
            if s != '':
                v = float(s)
                if 0 <= v <= 100:
                    ext_min_score = v / 100.0
        except (ValueError, TypeError):
            pass

    fn_filters = []
    speaker_filter = (request.GET.get('speaker') or '').strip()
    # Фильтр «скрывать почти пустые» — включён по умолчанию, ?hide_empty=0 отключает
    hide_empty = request.GET.get('hide_empty', '1') != '0'
    # Фильтр «три последних конструктивных» — включён по умолчанию, ?last3=0 отключает
    last3 = request.GET.get('last3', '1') != '0'
    # Фильтр «только мои» — ?mine=1
    only_mine = request.GET.get('mine') == '1'
    date_str = request.GET.get('date') or timezone.localtime(timezone.now()).strftime('%Y-%m-%d')
    date_to_str = request.GET.get('date_to', '').strip()
    time_from = request.GET.get('time_from', '')
    time_to = request.GET.get('time_to', '')
    recordings = []
    semantic_results = []

    from django.db.models import Q
    bp_slug = settings.BP_SPACE_SLUG
    if current_user and current_user.space:
        if current_user.space.slug == bp_slug:
            other_space_ids = Space.objects.exclude(slug=bp_slug).values_list('id', flat=True)
            base_qs = Recording.objects.exclude(space_id__in=other_space_ids)
        else:
            base_qs = Recording.objects.filter(space=current_user.space)
    else:
        base_qs = Recording.objects.filter(space__isnull=True)

    # Скрываем личные записи других пользователей
    if current_user:
        base_qs = base_qs.filter(Q(is_personal=False) | Q(owner=current_user))
    else:
        base_qs = base_qs.filter(is_personal=False)

    # Фильтр «только мои»
    if only_mine and current_user:
        base_qs = base_qs.filter(owner=current_user, is_personal=True)

    if tab == 'ft' and ft_q:
        try:
            recordings = list(fulltext_search(base_qs, ft_q, search_comments=search_comments)[:100])
        except OperationalError:
            pass
    elif tab == 'ext' and ext_q:
        try:
            semantic_results = semantic_search(ext_q, limit=ext_limit, min_score=ext_min_score)
            recordings = [rec for rec, _ in semantic_results]
        except Exception:
            pass
    elif last3:
        # Три последних конструктивных: любые записи с непустой транскрипцией, без ограничений по дате
        try:
            from django.db.models import Q as _Q
            qs = base_qs.filter(
                _Q(transcription__isnull=False) & ~_Q(transcription='')
            )
            if speaker_filter:
                from django.db.models.functions import Cast
                from django.db.models import TextField
                qs = qs.annotate(
                    _snames=Cast('speaker_names', TextField())
                ).filter(_snames__icontains=speaker_filter)
            recordings = list(qs.order_by('-created_at')[:3])
        except OperationalError:
            pass
    else:
        try:
            tz = timezone.get_current_timezone()
            today = timezone.now().date()
            try:
                d = datetime.strptime(date_str, '%Y-%m-%d')
                day_start = timezone.make_aware(datetime.combine(d.date(), datetime.min.time()), tz)
            except ValueError:
                day_start = timezone.make_aware(datetime.combine(today, datetime.min.time()), tz)

            if date_to_str:
                try:
                    d_end = datetime.strptime(date_to_str, '%Y-%m-%d')
                    day_end = timezone.make_aware(datetime.combine(d_end.date(), datetime.max.time()), tz)
                except ValueError:
                    day_end = day_start + timezone.timedelta(days=1)
            else:
                day_end = day_start + timezone.timedelta(days=1)

            qs = base_qs.filter(created_at__gte=day_start, created_at__lte=day_end)

            if time_from:
                try:
                    h, m = map(int, time_from.split(':'))
                    dt = timezone.make_aware(datetime.combine(day_start.date(), datetime.min.time().replace(hour=h, minute=m)), tz)
                    qs = qs.filter(created_at__gte=dt)
                except (ValueError, AttributeError):
                    pass
            if time_to:
                try:
                    h, m = map(int, time_to.split(':'))
                    dt = timezone.make_aware(datetime.combine(day_start.date(), datetime.min.time().replace(hour=h, minute=m)), tz)
                    qs = qs.filter(created_at__lt=dt)
                except (ValueError, AttributeError):
                    pass

            if hide_empty:
                from django.db.models import Q as _Q
                # Скрываем done-записи без текста; pending/stable/transcribing/failed всегда показываем
                qs = qs.filter(
                    _Q(status__in=[
                        Recording.Status.PENDING, Recording.Status.STABLE,
                        Recording.Status.TRANSCRIBING, Recording.Status.FAILED,
                    ]) |
                    (
                        _Q(transcription__isnull=False) &
                        ~_Q(transcription='') &
                        _Q(transcription__regex=r'.{100,}')
                    )
                )

            if fn_filters:
                q = Q()
                for s in fn_filters:
                    q |= Q(filename__icontains=s)
                qs = qs.filter(q)

            if speaker_filter:
                from django.db.models.functions import Cast
                from django.db.models import TextField
                qs = qs.annotate(
                    _snames=Cast('speaker_names', TextField())
                ).filter(_snames__icontains=speaker_filter)

            recordings = list(qs.order_by('-created_at'))
        except OperationalError:
            pass

    if recordings:
        counts = dict(
            Recording.objects.filter(pk__in=[r.pk for r in recordings])
            .annotate(c=Count('comments'))
            .values_list('pk', 'c')
        )
        for r in recordings:
            r.comment_count = counts.get(r.pk, 0)

    try:
        db_tag_list = list(TagDefinition.objects.values_list('slug', 'label'))
    except Exception:
        db_tag_list = []
    tag_choices = db_tag_list if db_tag_list else [(k, v) for k, v in Recording.TAG_CHOICES if k]

    return render(request, 'recordings/index.html', {
        'recordings': recordings,
        'semantic_results': semantic_results,
        'active_tab': tab,
        'ft_q': ft_q,
        'ext_q': ext_q,
        'ext_limit': ext_limit,
        'ext_min_score_pct': int(ext_min_score * 100),
        'search_comments': search_comments,
        'semantic_available': _semantic_available(),
        'selected_date': date_str,
        'date_to': date_to_str,
        'time_from': time_from,
        'time_to': time_to,
        'speaker_filter': speaker_filter,
        'space_speaker_profiles': _collect_speaker_names(current_user, base_qs),
        'tag_choices': tag_choices,
        'fn_filters': fn_filters,
        'hide_empty': hide_empty,
        'last3': last3,
        'only_mine': only_mine,
        'fn_options': [('cpq', 'CPQ'), ('daily', 'Daily'), ('bp', 'BP'), ('analytics', 'Analytics'), ('demo', 'Demo')],
        'user_space_slug': user_space_slug,
        'current_user': current_user,
    })


@site_login_required
@require_http_methods(['POST'])
def set_recording_tag(request, recording_id):
    """Установить тег записи с главной страницы."""
    rec = get_object_or_404(Recording, pk=recording_id)
    tag = (request.POST.get('tag') or '').strip()
    if tag == '' or tag in dict(Recording.TAG_CHOICES):
        rec.tag = tag
        rec.save(update_fields=['tag'])
        messages.success(request, f'Тег для «{rec.filename}» обновлён.')
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('recordings:index')
    return redirect(next_url)


@site_login_required
def log_list(request):
    from django.db.utils import OperationalError
    try:
        logs = list(PollLog.objects.order_by('-started_at')[:200])
    except OperationalError:
        logs = []
    return render(request, 'recordings/logs.html', {'logs': logs})


@site_login_required
@require_http_methods(['POST'])
def run_transcription(request):
    """Принудительно запустить один цикл опроса S3 и транскрибации (в фоне)."""
    def _run():
        try:
            services.run_poll_and_transcribe()
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    messages.success(
        request,
        'Транскрибация запущена в фоне. Обновите страницу через минуту, чтобы увидеть логи и новые записи.',
    )
    next_url = reverse('recordings:index')
    date = request.POST.get('date') or request.GET.get('date')
    if date:
        next_url += '?date=' + date
    return redirect(next_url)


def _build_support_email_body(rec, logs):
    lines = [
        'Ошибка транскрибации записи',
        '',
        f'Файл: {rec.filename}',
        f'S3 ключ: {rec.s3_key}',
        f'Дата: {rec.created_at.strftime("%d.%m.%Y %H:%M")}',
        f'Ошибка: {rec.error_message or "(нет текста)"}',
        '',
        '--- Последние логи опроса ---',
    ]
    for log in logs:
        status = 'OK' if log.success else 'ОШИБКА'
        lines.append(
            f"{log.started_at.strftime('%d.%m.%Y %H:%M:%S')} | {status} | "
            f"найдено: {log.files_found}, стабильных: {log.files_stable}, транскрибировано: {log.files_transcribed}"
        )
        if log.message:
            lines.append(f"  {log.message}")
    return '\n'.join(lines)


@site_login_required
def support_request(request, recording_id):
    """Страница с кнопкой «Поделиться ошибкой» — открывает почту (hexneo36@gmail.com) с логами."""
    rec = get_object_or_404(Recording, pk=recording_id)
    logs = list(PollLog.objects.order_by('-started_at')[:25])
    to_email = request.GET.get('email') or getattr(settings, 'SHARE_ERROR_EMAIL', 'hexneo36@gmail.com')
    body_text = _build_support_email_body(rec, logs)
    subject = f'Ошибка транскрибации: {rec.filename}'
    return render(request, 'recordings/support_email.html', {
        'recording': rec,
        'logs': logs,
        'support_email': to_email,
        'body_text': body_text,
        'subject': subject,
    })


@site_login_required
def recording_detail(request, recording_id):
    """Страница записи: транскрипция, скачать, поделиться ссылкой, комментарии."""
    import re as _re
    import json as _json
    rec = get_object_or_404(Recording, pk=recording_id)
    current_user = get_current_user(request)
    if rec.is_personal and rec.owner_id != (current_user.pk if current_user else None):
        from django.http import Http404
        raise Http404
    _log_access(request, AccessLog.EVENT_VIEW, recording=rec)
    comments = list(rec.comments.all())
    download_url = None
    if rec.s3_key:
        try:
            download_url = get_presigned_download_url(rec.s3_key, rec.filename, expires_in=300)
        except Exception:
            pass

    speaker_profiles = list(rec.space.speaker_profiles.values_list('name', flat=True)) if rec.space else []

    # Автоподбор имён по речевым паттернам если имена ещё не сохранены
    auto_names = {}
    if rec.space and not rec.speaker_names and rec.transcription:
        speaker_ids = list(set(_re.findall(r'^[\-—]\s*(.+?):', rec.transcription, _re.MULTILINE)))
        if speaker_ids:
            try:
                auto_names = services.match_by_speech_patterns(rec.space, rec.transcription, speaker_ids)
            except Exception:
                pass

    # Собрать все уникальные имена из записей пространства для подсказок
    all_names = set(speaker_profiles)
    for names_dict in rec.space.recordings.exclude(speaker_names={}).values_list('speaker_names', flat=True) if rec.space else []:
        if isinstance(names_dict, dict):
            all_names.update(v for v in names_dict.values() if v and isinstance(v, str))

    hf_token_set = bool(getattr(settings, 'HUGGINGFACE_TOKEN', ''))
    download_logs = list(
        rec.access_logs.filter(event=AccessLog.EVENT_DOWNLOAD).order_by('-created_at')[:20]
    )
    return render(request, 'recordings/recording_detail.html', {
        'recording': rec,
        'comments': comments,
        'download_url': download_url,
        'speaker_profiles': sorted(all_names),
        'auto_names_json': _json.dumps(auto_names, ensure_ascii=False),
        'current_user': current_user,
        'lib_versions': services.get_lib_versions(),
        'hf_token_set': hf_token_set,
        'download_logs': download_logs,
    })


@site_login_required
@require_http_methods(['POST'])
def create_share_token(request, recording_id):
    """Создать публичную (анонимную) ссылку на запись."""
    rec = get_object_or_404(Recording, pk=recording_id)
    token = ShareToken.objects.create(recording=rec)
    url = request.build_absolute_uri(
        reverse('recordings:shared_recording', args=[str(token.token)])
    )
    return JsonResponse({'url': url, 'token': str(token.token)})


def shared_recording(request, token):
    """Публичная страница записи без авторизации."""
    share = get_object_or_404(ShareToken, token=token, is_active=True)
    rec = share.recording
    download_url = None
    if rec.s3_key:
        try:
            download_url = get_presigned_download_url(rec.s3_key, rec.filename, expires_in=300)
        except Exception:
            pass
    comments = list(rec.comments.all())
    return render(request, 'recordings/shared_recording.html', {
        'recording': rec,
        'comments': comments,
        'download_url': download_url,
    })


@site_login_required
@require_http_methods(['POST'])
def run_recording_transcription(request, recording_id):
    """Поставить задачу в очередь. Параметры: stage, quality, language, device."""
    from .queue_services import enqueue_transcribe, enqueue_embedding
    rec = get_object_or_404(Recording, pk=recording_id)

    stage = request.POST.get('stage', 'transcription')  # transcription | ai_summary | embedding
    device = request.POST.get('device', 'auto')          # auto | cpu | gpu (legacy)
    # Новый combined-param: "whisperx:small:gpu" / "faster:medium:cpu" / etc.
    engine_param = request.POST.get('engine', '')        # engine:model:device

    next_url = request.POST.get('next') or request.GET.get('next') or reverse('recordings:recording_detail', args=[rec.pk])

    if stage == 'ai_summary':
        if not rec.transcription:
            messages.error(request, 'Нет транскрипции для генерации резюме.')
            return redirect(next_url)
        def _run_summary():
            try:
                services.generate_ai_summary(rec)
            except Exception:
                pass
        threading.Thread(target=_run_summary, daemon=True).start()
        messages.success(request, 'Генерация AI-заголовка и резюме запущена.')
        return redirect(next_url)

    if stage == 'embedding':
        if not rec.transcription:
            messages.error(request, 'Нет транскрипции для индексации.')
            return redirect(next_url)
        enqueue_embedding(rec)
        def _run_emb():
            try:
                from .queue_services import index_embedding_for_recording
                index_embedding_for_recording(rec)
            except Exception:
                pass
        threading.Thread(target=_run_emb, daemon=True).start()
        messages.success(request, 'Индексация эмбеддинга поставлена в очередь.')
        return redirect(next_url)

    # stage == 'transcription' (default)
    lang = request.POST.get('language')
    update_fields = []

    # Парсим engine param (новый формат: "whisperx:small:gpu" / "faster:medium:cpu")
    if engine_param:
        parts = engine_param.split(':')
        eng = parts[0] if len(parts) > 0 else ''  # whisperx | faster
        mdl = parts[1] if len(parts) > 1 else 'small'
        dev = parts[2] if len(parts) > 2 else 'auto'
        if eng in ('whisperx', 'faster'):
            SystemConfig.set(f'engine_override_{rec.pk}', eng)
        if mdl:
            SystemConfig.set(f'model_override_{rec.pk}', mdl)
            if mdl in dict(Recording.QUALITY_CHOICES):
                rec.transcription_quality = mdl
                update_fields.append('transcription_quality')
        if dev in ('cpu', 'gpu'):
            SystemConfig.set(f'device_override_{rec.pk}', dev)
    else:
        # Обратная совместимость: старые поля quality / device
        quality = request.POST.get('quality')
        if quality in dict(Recording.QUALITY_CHOICES):
            rec.transcription_quality = quality
            update_fields.append('transcription_quality')
        if device in ('cpu', 'gpu'):
            SystemConfig.set(f'device_override_{rec.pk}', device)

    if lang in dict(Recording.LANGUAGE_CHOICES):
        rec.transcription_language = lang
        update_fields.append('transcription_language')

    rec.transcription = ''
    rec.status = Recording.Status.STABLE
    update_fields.extend(['transcription', 'status'])
    rec.save(update_fields=update_fields)

    SystemConfig.set(f'trigger_{rec.pk}', 'manual')
    enqueue_transcribe(rec, priority=1)

    messages.success(request, f'Запись «{rec.filename}» добавлена в очередь транскрибации.')
    return redirect(next_url)


@site_login_required
@site_login_required
def download_recording(request, recording_id):
    """Редирект на временную ссылку скачивания MP3 из S3. Логирует скачивание."""
    rec = get_object_or_404(Recording, pk=recording_id)
    current_user = get_current_user(request)
    if rec.is_personal and rec.owner_id != (current_user.pk if current_user else None):
        from django.http import Http404
        raise Http404
    _log_access(request, AccessLog.EVENT_DOWNLOAD, recording=rec)
    url = get_presigned_download_url(rec.s3_key, rec.filename, expires_in=60)
    return redirect(url)


@site_login_required
def download_transcription(request, recording_id):
    """Скачать результат транскрибации в виде .txt файла."""
    rec = get_object_or_404(Recording, pk=recording_id)
    if not rec.transcription:
        messages.warning(request, 'Транскрипция ещё не готова.')
        return redirect(reverse('recordings:recording_detail', args=[rec.pk]))
    from django.http import HttpResponse
    response = HttpResponse(rec.transcription, content_type='text/plain; charset=utf-8')
    filename = rec.filename.rsplit('.', 1)[0] + '_transcription.txt'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@site_login_required
@require_http_methods(['POST'])
def add_comment(request, recording_id):
    """Добавить комментарий к записи."""
    rec = get_object_or_404(Recording, pk=recording_id)
    text = (request.POST.get('text') or '').strip()
    if text:
        Comment.objects.create(recording=rec, text=text)
        messages.success(request, 'Комментарий добавлен.')
    else:
        messages.warning(request, 'Введите текст комментария.')
    return redirect(reverse('recordings:recording_detail', args=[rec.pk]))


@site_login_required
@require_http_methods(['GET', 'POST'])
def uploads_page(request):
    """Загрузка видео/аудио в S3 и очередь транскрибации."""
    from .queue_services import enqueue_transcribe
    import os
    import tempfile
    import uuid
    current_user = get_current_user(request)
    user_space_slug = current_user.space.slug if (current_user and current_user.space) else ''
    if request.method == 'GET':
        return render(request, 'recordings/uploads.html', {
            'current_user': current_user,
            'user_space_slug': user_space_slug,
        })
    f = request.FILES.get('video')
    if not f:
        messages.warning(request, 'Выберите файл.')
        return redirect(reverse('recordings:uploads'))
    # Free tier check
    if current_user and current_user.free_left is not None:
        if current_user.free_left <= 0:
            messages.error(request, 'Лимит бесплатных транскрибаций исчерпан.')
            return redirect(reverse('recordings:uploads'))
    prefix = (getattr(settings, 'S3_PREFIX', None) or '').strip()
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    safe_name = (f.name or 'audio').replace('/', '_').replace('\\', '_')[-200:]
    ext = os.path.splitext(safe_name)[1].lower()
    if ext != '.mp3':
        safe_name = os.path.splitext(safe_name)[0] + '.mp3'
    s3_key = f"{prefix}{uuid.uuid4().hex}_{safe_name}"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f.name)[1] or '.bin') as tmp:
            for chunk in f.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            if ext != '.mp3':
                mp3_path = tmp_path + '.mp3'
                try:
                    import subprocess
                    subprocess.run(['ffmpeg', '-y', '-i', tmp_path, '-acodec', 'libmp3lame', '-q:a', '2', mp3_path],
                        check=True, capture_output=True, timeout=300)
                    os.remove(tmp_path)
                    tmp_path = mp3_path
                except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    messages.error(request, 'Конвертация в MP3 не удалась (нужен ffmpeg). Загрузите MP3.')
                    return redirect(reverse('recordings:uploads'))
            upload_file_to_s3(tmp_path, s3_key, content_type='audio/mpeg')
            size = os.path.getsize(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        is_personal = request.POST.get('is_personal') == '1'
        rec = Recording.objects.create(
            s3_key=s3_key,
            filename=os.path.basename(safe_name),
            size_bytes=size,
            status=Recording.Status.STABLE,
            space=current_user.space if current_user else None,
            owner=current_user if current_user else None,
            is_personal=is_personal,
        )
        # Decrement free tier counter
        if current_user and current_user.free_left is not None:
            current_user.free_left -= 1
            current_user.save(update_fields=['free_left'])
        enqueue_transcribe(rec, priority=1)
        messages.success(request, f'Файл «{rec.filename}» загружен и поставлен в очередь транскрибации.')
    except Exception as e:
        messages.error(request, f'Ошибка загрузки: {e}')
    return redirect(reverse('recordings:uploads'))


@site_login_required
@require_http_methods(['GET', 'POST'])
def ocr_page(request):
    """OCR: форма загрузки и список задач."""
    from django.db.utils import OperationalError
    import os
    import tempfile
    current_user = get_current_user(request)
    jobs = []
    ocr_q = ''
    ocr_date = ''
    ocr_date_to = ''
    try:
        from django.db.models import Q as DQ
        bp_slug = settings.BP_SPACE_SLUG
        if current_user and current_user.space:
            if current_user.space.slug == bp_slug:
                other_ids = Space.objects.exclude(slug=bp_slug).values_list('id', flat=True)
                base_qs = OcrJob.objects.exclude(space_id__in=other_ids)
            else:
                base_qs = OcrJob.objects.filter(space=current_user.space)
        else:
            base_qs = OcrJob.objects.filter(space__isnull=True)

        ocr_q = request.GET.get('q', '').strip()
        ocr_date = request.GET.get('date', '').strip()
        ocr_date_to = request.GET.get('date_to', '').strip()

        qs = base_qs.order_by('-created_at')
        if ocr_q:
            qs = qs.filter(
                DQ(original_filename__icontains=ocr_q) |
                DQ(result_markdown__icontains=ocr_q)
            )
        if ocr_date:
            from datetime import datetime as _dt
            try:
                d_from = _dt.strptime(ocr_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=d_from)
            except ValueError:
                pass
        if ocr_date_to:
            from datetime import datetime as _dt
            try:
                d_to = _dt.strptime(ocr_date_to, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=d_to)
            except ValueError:
                pass
        jobs = list(qs[:200])
    except OperationalError:
        pass
    ocr_configured = bool(getattr(settings, 'OCR_API_URL', None))
    if request.method == 'GET':
        return render(request, 'recordings/ocr.html', {
            'jobs': jobs,
            'ocr_configured': ocr_configured,
            'user_space': current_user.space if current_user else None,
            'ocr_q': ocr_q,
            'ocr_date': ocr_date,
            'ocr_date_to': ocr_date_to,
        })
    files = request.FILES.getlist('documents')
    if not files:
        # fallback: legacy single-file field name
        single = request.FILES.get('document')
        if single:
            files = [single]
    if not files:
        messages.warning(request, 'Выберите файл (PDF или изображение).')
        return redirect(reverse('recordings:ocr'))
    _mime_ext = {
        'image/jpeg': '.jpg', 'image/jpg': '.jpg',
        'image/png': '.png', 'application/pdf': '.pdf',
    }
    ocr_method = request.POST.get('method', 'auto')
    if ocr_method not in ('auto', 'tesseract', 'olmocr'):
        ocr_method = 'auto'
    jobs_created = []
    for f in files:
        suffix = os.path.splitext(f.name or '')[1].lower()
        if not suffix:
            suffix = _mime_ext.get((f.content_type or '').split(';')[0].strip().lower(), '.bin')
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in f.chunks():
                tmp.write(chunk)
            path = tmp.name
        job = OcrJob.objects.create(
            original_filename=f.name or 'document',
            file_path=path,
            status='pending',
            space=current_user.space if current_user else None,
        )
        jobs_created.append((job, path, ocr_method))

    def _run_queue(items):
        for job, path, method in items:
            try:
                _run_ocr_job(job, method=method)
            except Exception:
                pass
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            job.file_path = ''
            try:
                job.save(update_fields=['file_path'])
            except Exception:
                pass

    threading.Thread(target=_run_queue, args=(jobs_created,), daemon=True).start()

    if len(jobs_created) == 1:
        return redirect(reverse('recordings:ocr_job_detail', args=[jobs_created[0][0].id]))
    messages.success(request, f'Добавлено {len(jobs_created)} файлов в очередь OCR.')
    return redirect(reverse('recordings:ocr'))


def _run_ocr_job(job, method='auto'):
    """Вызвать OCR API и обновить job."""
    from .ocr_api import call_ocr_api
    job.status = 'processing'
    job.save(update_fields=['status'])
    text, err = call_ocr_api(job.file_path, method=method)
    if err:
        job.status = 'failed'
        job.error_message = err
        job.save(update_fields=['status', 'error_message'])
        return
    job.status = 'done'
    job.result_markdown = text or ''
    job.error_message = ''
    job.save(update_fields=['status', 'result_markdown', 'error_message'])


@site_login_required
@require_http_methods(['GET', 'POST'])
def ocr_job_detail(request, job_id):
    """Детальная страница задачи OCR."""
    import uuid as _uuid_mod
    job = get_object_or_404(OcrJob, pk=job_id)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'share_toggle':
            if job.is_public:
                job.is_public = False
                job.save(update_fields=['is_public'])
            else:
                if not job.share_token:
                    job.share_token = _uuid_mod.uuid4()
                job.is_public = True
                job.save(update_fields=['share_token', 'is_public'])
        return redirect(reverse('recordings:ocr_job_detail', args=[job.id]))
    share_url = None
    if job.is_public and job.share_token:
        share_url = request.build_absolute_uri(
            reverse('recordings:ocr_share', args=[str(job.share_token)])
        )
    return render(request, 'recordings/ocr_job_detail.html', {'job': job, 'share_url': share_url})


def ocr_share(request, token):
    """Публичный просмотр результата OCR по токену."""
    job = get_object_or_404(OcrJob, share_token=token, is_public=True)
    return render(request, 'recordings/ocr_share.html', {'job': job})


@site_login_required
@require_http_methods(['GET', 'POST'])
def space_members(request):
    """Управление участниками пространства (добавить нового)."""
    from .user_service import get_or_create_user, set_password_no_email
    current_user = get_current_user(request)
    if not current_user or not current_user.space:
        return redirect(reverse('recordings:index'))
    space = current_user.space
    error = None
    new_creds = None  # (email, password) — показать после добавления
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        if not email:
            error = 'Введите email.'
        else:
            user, _ = get_or_create_user(email)
            if user.space and user.space != space:
                error = f'Пользователь {email} уже принадлежит другому пространству.'
            else:
                if user.space != space:
                    user.space = space
                    user.free_left = None
                    user.save(update_fields=['space', 'free_left'])
                plain = set_password_no_email(user)
                new_creds = (email, plain) if plain else (email, None)
    members = list(space.members.order_by('email'))
    return render(request, 'recordings/space_members.html', {
        'space': space,
        'members': members,
        'error': error,
        'new_creds': new_creds,
    })


def api_space_ocr(request, api_key):
    """Публичный API: список выполненных OCR задач для пространства.
    GET /api/space/{api_key}/ocr/
    Возвращает JSON список завершённых задач.
    """
    try:
        space = Space.objects.get(api_key=api_key)
    except (Space.DoesNotExist, Exception):
        return JsonResponse({'error': 'Invalid API key'}, status=401)
    jobs = OcrJob.objects.filter(space=space, status='done').order_by('-created_at')[:100]
    return JsonResponse({
        'space': space.name,
        'results': [
            {
                'id': j.id,
                'filename': j.original_filename,
                'created_at': j.created_at.isoformat(),
                'text': j.result_markdown,
            }
            for j in jobs
        ]
    })


# ─── OCR публичный API ──────────────────────────────────────────────────────

def _check_ocr_api_key(request):
    """Вернуть None если ключ верен, иначе JsonResponse с ошибкой."""
    import os as _os
    api_key = getattr(settings, 'OCR_PUBLIC_API_KEY', '') or _os.environ.get('OCR_PUBLIC_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'OCR API not configured (set OCR_PUBLIC_API_KEY in .env)'}, status=503)
    if request.META.get('HTTP_X_API_KEY', '') != api_key:
        return JsonResponse({'error': 'Unauthorized: provide valid X-Api-Key header'}, status=401)
    return None


@csrf_exempt
@require_http_methods(['POST'])
def api_ocr_submit(request):
    """API: отправить файл на OCR → получить task_id.
    POST /api/ocr/submit/
    Header: X-Api-Key: <OCR_PUBLIC_API_KEY>
    Body: multipart/form-data, field 'file'
    Response: {"task_id": 123, "status": "pending"}
    """
    import os
    import tempfile
    err = _check_ocr_api_key(request)
    if err:
        return err
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file provided (use field name "file")'}, status=400)
    _mime_ext = {
        'image/jpeg': '.jpg', 'image/jpg': '.jpg',
        'image/png': '.png', 'application/pdf': '.pdf',
    }
    suffix = os.path.splitext(f.name or '')[1].lower()
    if not suffix:
        suffix = _mime_ext.get((f.content_type or '').split(';')[0].strip().lower(), '.bin')
    if suffix not in ('.pdf', '.png', '.jpg', '.jpeg'):
        return JsonResponse({'error': 'Only PDF, PNG, JPEG allowed'}, status=400)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        for chunk in f.chunks():
            tmp.write(chunk)
        path = tmp.name
    job = OcrJob.objects.create(original_filename=f.name or 'document', file_path=path, status='pending')

    def _run_bg():
        try:
            _run_ocr_job(job)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            job.file_path = ''
            job.save(update_fields=['file_path'])

    threading.Thread(target=_run_bg, daemon=True).start()
    return JsonResponse({'task_id': job.id, 'status': job.status})


@require_http_methods(['GET'])
def api_ocr_status(request, task_id):
    """API: получить статус задачи OCR.
    GET /api/ocr/status/<task_id>/
    Header: X-Api-Key: <OCR_PUBLIC_API_KEY>
    Response: {"task_id": N, "status": "pending|processing|done|failed", "markdown": "...", "error": "..."}
    """
    err = _check_ocr_api_key(request)
    if err:
        return err
    job = get_object_or_404(OcrJob, pk=task_id)
    resp = {'task_id': job.id, 'status': job.status}
    if job.status == OcrJob.Status.DONE:
        resp['markdown'] = job.result_markdown
    elif job.status == OcrJob.Status.FAILED:
        resp['error'] = job.error_message
    return JsonResponse(resp)


# ─── V1 API ─────────────────────────────────────────────────────────────────

def _check_master_key(request):
    key = getattr(settings, 'MASTER_API_KEY', '')
    if not key:
        return JsonResponse({'error': 'MASTER_API_KEY not configured'}, status=503)
    if request.META.get('HTTP_X_API_KEY', '') != key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    return None


def _check_space_key(request):
    """Проверить X-Api-Key как UUID пространства. Возвращает (space, None) или (None, response)."""
    raw = request.META.get('HTTP_X_API_KEY', '').strip()
    if not raw:
        return None, JsonResponse({'error': 'Missing X-Api-Key header'}, status=401)
    try:
        import uuid as _uuid_mod
        space = Space.objects.get(api_key=_uuid_mod.UUID(raw))
        return space, None
    except (Space.DoesNotExist, ValueError):
        return None, JsonResponse({'error': 'Invalid API key'}, status=401)


@csrf_exempt
@require_http_methods(['POST'])
def api_v1_org_create(request):
    """
    POST /api/v1/org/
    Header: X-Api-Key: <MASTER_API_KEY>
    Body JSON: {"name": "Ромашка", "email": "admin@romashka.ru"}
    Response: {
        "org_id": 1, "name": "Ромашка", "slug": "romashka-xxxx",
        "api_key": "uuid", "email": "...", "password": "...",
        "magic_link": "https://..."
    }
    """
    import json as _json
    import datetime as _dt
    from .user_service import get_or_create_user
    from .email_service import generate_password
    from .telegram_service import make_unique_space_slug

    err = _check_master_key(request)
    if err:
        return err

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (body.get('name') or '').strip()
    email = (body.get('email') or '').strip().lower()
    if not name or not email:
        return JsonResponse({'error': 'name and email are required'}, status=400)

    if SiteUser.objects.filter(email__iexact=email).exists():
        return JsonResponse({'error': f'User {email} already exists'}, status=409)

    slug = make_unique_space_slug(name)
    space = Space.objects.create(name=name, slug=slug)

    pwd = generate_password()
    user = SiteUser.objects.create(email=email, space=space, free_left=5)
    user.set_password(pwd)
    user.save(update_fields=['password'])

    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
    magic = MagicLoginToken.objects.create(
        user=user,
        expires_at=timezone.now() + _dt.timedelta(days=7),
    )
    magic_url = f'{site_url}/magic-login/{magic.token}/'

    return JsonResponse({
        'org_id': space.pk,
        'name': space.name,
        'slug': space.slug,
        'api_key': str(space.api_key),
        'email': email,
        'password': pwd,
        'magic_link': magic_url,
    }, status=201)


@csrf_exempt
@require_http_methods(['POST'])
def api_v1_ocr_submit(request):
    """
    POST /api/v1/ocr/
    Header: X-Api-Key: <space.api_key>
    Body JSON: {"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}
    Response: {"task_id": 123, "status": "pending"}
    """
    import json as _json
    import tempfile
    import os
    import requests as req

    space, err = _check_space_key(request)
    if err:
        return err

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    url = (body.get('url') or '').strip()
    filename = (body.get('filename') or '').strip() or 'document'
    if not url:
        return JsonResponse({'error': '"url" is required'}, status=400)

    # Определяем расширение
    import os.path as _op
    ext = _op.splitext(filename)[1].lower()
    if ext not in ('.pdf', '.png', '.jpg', '.jpeg'):
        # попробуем угадать из URL
        ext = _op.splitext(url.split('?')[0])[1].lower()
    if ext not in ('.pdf', '.png', '.jpg', '.jpeg'):
        ext = '.pdf'

    # Скачиваем файл
    try:
        resp = req.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        content_length = int(resp.headers.get('content-length', 0))
        if content_length > 50 * 1024 * 1024:  # 50 МБ
            return JsonResponse({'error': 'File too large (max 50 MB)'}, status=413)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            size = 0
            for chunk in resp.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    os.remove(tmp.name)
                    return JsonResponse({'error': 'File too large (max 50 MB)'}, status=413)
                tmp.write(chunk)
            path = tmp.name
    except Exception as e:
        return JsonResponse({'error': f'Failed to download file: {e}'}, status=400)

    job = OcrJob.objects.create(
        original_filename=filename,
        file_path=path,
        status=OcrJob.Status.PENDING,
        space=space,
    )

    def _run_bg():
        try:
            _run_ocr_job(job)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            job.file_path = ''
            job.save(update_fields=['file_path'])

    threading.Thread(target=_run_bg, daemon=True).start()
    return JsonResponse({'task_id': job.id, 'status': job.status}, status=202)


@require_http_methods(['GET'])
def api_v1_ocr_status(request, task_id):
    """
    GET /api/v1/ocr/<task_id>/
    Header: X-Api-Key: <space.api_key>
    Response: {"task_id": N, "status": "pending|processing|done|failed", "markdown": "..."}
    """
    space, err = _check_space_key(request)
    if err:
        return err
    try:
        job = OcrJob.objects.get(pk=task_id, space=space)
    except OcrJob.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)

    resp = {'task_id': job.id, 'status': job.status, 'filename': job.original_filename}
    if job.status == OcrJob.Status.DONE:
        resp['markdown'] = job.result_markdown
    elif job.status == OcrJob.Status.FAILED:
        resp['error'] = job.error_message
    return JsonResponse(resp)


# ─── Регистрация сторонних организаций через Telegram ───────────────────────

def org_register(request):
    """Форма регистрации сторонней организации. Подтверждение — через Telegram-бот."""
    import random
    import string

    from .telegram_service import get_bot_info

    error = None
    success_code = None
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''

    if not bot_username:
        info = get_bot_info()
        bot_username = info.get('username', '')

    if request.method == 'POST':
        org_name = request.POST.get('org_name', '').strip()
        email = request.POST.get('email', '').strip().lower()

        if not org_name or not email:
            error = 'Заполните все поля.'
        elif SiteUser.objects.filter(email__iexact=email).exists():
            error = 'Пользователь с таким email уже зарегистрирован.'
        elif OrgRegistration.objects.filter(email__iexact=email, status='pending').exists():
            # Показываем уже выданный код
            reg = OrgRegistration.objects.filter(email__iexact=email, status='pending').first()
            success_code = reg.verify_code
        else:
            chars = string.ascii_uppercase + string.digits
            code = ''.join(random.choices(chars, k=8))
            while OrgRegistration.objects.filter(verify_code=code).exists():
                code = ''.join(random.choices(chars, k=8))
            OrgRegistration.objects.create(org_name=org_name, email=email, verify_code=code)
            success_code = code

    return render(request, 'recordings/org_register.html', {
        'error': error,
        'success_code': success_code,
        'bot_username': bot_username,
    })


@csrf_exempt
@require_http_methods(['POST', 'OPTIONS'])
def api_org_register_public(request):
    """JSON API для регистрации организации (используется standalone-лендингом Baza)."""
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }

    if request.method == 'OPTIONS':
        resp = JsonResponse({})
        for k, v in cors_headers.items():
            resp[k] = v
        return resp

    import json as _json, random, string
    from .telegram_service import get_bot_info

    try:
        data = _json.loads(request.body)
    except Exception:
        data = {}

    org_name = (data.get('org_name') or request.POST.get('org_name', '')).strip()
    email = (data.get('email') or request.POST.get('email', '')).strip().lower()

    def _resp(payload, status=200):
        r = JsonResponse(payload, status=status)
        for k, v in cors_headers.items():
            r[k] = v
        return r

    if not org_name or not email:
        return _resp({'ok': False, 'error': 'Заполните все поля.'}, 400)
    if SiteUser.objects.filter(email__iexact=email).exists():
        return _resp({'ok': False, 'error': 'Пользователь с таким email уже зарегистрирован.'}, 400)

    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''
    if not bot_username:
        bot_username = get_bot_info().get('username', '')

    if OrgRegistration.objects.filter(email__iexact=email, status='pending').exists():
        reg = OrgRegistration.objects.filter(email__iexact=email, status='pending').first()
        return _resp({'ok': True, 'code': reg.verify_code, 'bot_username': bot_username})

    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=8))
    while OrgRegistration.objects.filter(verify_code=code).exists():
        code = ''.join(random.choices(chars, k=8))
    OrgRegistration.objects.create(org_name=org_name, email=email, verify_code=code)
    return _resp({'ok': True, 'code': code, 'bot_username': bot_username})


@csrf_exempt
def tg_webhook(request, secret):
    """Telegram Bot webhook endpoint."""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    expected = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '')
    if not expected or secret != expected:
        return JsonResponse({'ok': False}, status=403)

    import json
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False}, status=400)

    try:
        from .telegram_service import handle_tg_update
        handle_tg_update(data)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error('tg_webhook handle error: %s', e, exc_info=True)

    return JsonResponse({'ok': True})


@csrf_exempt
def tg_custom_webhook(request, bot_pk, secret):
    """Webhook для кастомных пользовательских ботов."""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    from .models import CustomBot
    try:
        bot = CustomBot.objects.get(pk=bot_pk, webhook_secret=secret, is_active=True)
    except CustomBot.DoesNotExist:
        return JsonResponse({'ok': False}, status=403)

    import json
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False}, status=400)

    try:
        from .telegram_service import handle_custom_bot_update
        handle_custom_bot_update(data, bot)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error('tg_custom_webhook error bot=%s: %s', bot_pk, e, exc_info=True)

    return JsonResponse({'ok': True})


@never_cache
@require_http_methods(['GET'])
def magic_login(request, token):
    """Одноразовая ссылка авторизации из Telegram."""
    try:
        mt = MagicLoginToken.objects.select_related('user__space').get(token=token)
    except MagicLoginToken.DoesNotExist:
        return render(request, 'recordings/magic_login_error.html', {'reason': 'not_found'})
    if not mt.is_valid:
        return render(request, 'recordings/magic_login_error.html', {'reason': 'expired' if mt.used_at is None else 'used'})
    mt.used_at = timezone.now()
    mt.save(update_fields=['used_at'])
    request.session['user_id'] = mt.user.pk
    request.session.set_expiry(60 * 60 * 24 * 30)
    request.session.modified = True
    _log_access(request, AccessLog.EVENT_LOGIN)
    return redirect(reverse('recordings:index'))


@site_login_required
@require_http_methods(['GET'])
def cabinet(request):
    """Личный кабинет пользователя."""
    user = get_current_user(request)
    bp_slug = settings.BP_SPACE_SLUG
    is_bp = user.space and user.space.slug == bp_slug
    is_premium = is_bp or (user.free_left is None)

    # Статистика транскрибаций за всё время
    total_done = Recording.objects.filter(
        space=user.space, status=Recording.Status.DONE
    ).count() if user.space else Recording.objects.filter(
        space__isnull=True, status=Recording.Status.DONE
    ).count()

    return render(request, 'recordings/cabinet.html', {
        'user': user,
        'is_bp': is_bp,
        'is_premium': is_premium,
        'total_done': total_done,
    })


@site_login_required
def profile_settings(request):
    """Настройки профиля: отображаемое имя и аватар."""
    import uuid as _uuid2
    user = get_current_user(request)
    saved = False

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'profile':
            display_name = request.POST.get('display_name', '').strip()
            tz = request.POST.get('timezone', '').strip()
            user.display_name = display_name
            if tz:
                user.timezone = tz
            user.save(update_fields=['display_name', 'timezone'])
            saved = True
        elif action == 'avatar':
            avatar_file = request.FILES.get('avatar')
            if avatar_file:
                ext = avatar_file.name.rsplit('.', 1)[-1].lower() if '.' in avatar_file.name else 'jpg'
                s3_key = f'avatars/{user.pk}/{_uuid2.uuid4().hex}.{ext}'
                import tempfile, os
                with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
                    for chunk in avatar_file.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name
                try:
                    upload_file_to_s3(tmp_path, s3_key, content_type=avatar_file.content_type or 'image/jpeg')
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                # Generate view URL (no Content-Disposition, long expiry for avatars)
                from .s3_client import get_s3_client
                _s3 = get_s3_client()
                avatar_url = _s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': settings.S3_BUCKET, 'Key': s3_key},
                    ExpiresIn=604800,  # 7 days
                )
                user.avatar_url = avatar_url
                user.save(update_fields=['avatar_url'])
                return JsonResponse({'avatar_url': avatar_url})
            return JsonResponse({'error': 'No file'}, status=400)

    timezones = [
        ('Europe/Moscow', 'МСК — Москва (UTC+3)'),
        ('Europe/Podgorica', 'Черногория (UTC+1/+2)'),
        ('Europe/Belgrade', 'Сербия (UTC+1/+2)'),
        ('Europe/Kiev', 'Украина (UTC+2/+3)'),
        ('Europe/Minsk', 'Беларусь (UTC+3)'),
        ('Asia/Almaty', 'Казахстан Алматы (UTC+5)'),
        ('Asia/Tashkent', 'Узбекистан (UTC+5)'),
        ('Asia/Tbilisi', 'Грузия (UTC+4)'),
        ('Asia/Yerevan', 'Армения (UTC+4)'),
        ('Asia/Baku', 'Азербайджан (UTC+4)'),
        ('Asia/Dubai', 'ОАЭ Дубай (UTC+4)'),
        ('Asia/Novosibirsk', 'Новосибирск (UTC+7)'),
        ('Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'),
        ('Europe/London', 'Лондон (UTC+0/+1)'),
        ('Europe/Berlin', 'Берлин (UTC+1/+2)'),
        ('UTC', 'UTC'),
    ]
    return render(request, 'recordings/profile_settings.html', {'user': user, 'saved': saved, 'timezones': timezones})


@site_login_required
def calendar_settings(request):
    """Настройки участия в встречах и уведомлений."""
    user = get_current_user(request)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        meeting_id = data.get('meeting_id')
        attending = data.get('attending', False)
        # notify_before_minutes=0 means no notifications
        notify_before = max(0, int(data.get('notify_before_minutes', 15)))
        no_notify = bool(data.get('no_notify', False))
        if no_notify:
            notify_before = 0
        repeat_every = max(1, int(data.get('repeat_every_minutes', 1)))
        try:
            meeting = MeetingRoom.objects.get(pk=meeting_id)
        except MeetingRoom.DoesNotExist:
            return JsonResponse({'error': 'Meeting not found'}, status=404)
        if attending:
            MeetingAttendee.objects.update_or_create(
                user=user,
                meeting=meeting,
                defaults={
                    'notify_before_minutes': notify_before,
                    'repeat_every_minutes': repeat_every,
                },
            )
        else:
            MeetingAttendee.objects.filter(user=user, meeting=meeting).delete()
        return JsonResponse({'ok': True})

    # GET: list upcoming meetings
    from django.db.models import Q as _CalQ
    now = timezone.now()
    meetings = MeetingRoom.objects.filter(
        space=user.space,
    ).filter(
        _CalQ(scheduled_at__gte=now) | _CalQ(ended_at__isnull=True)
    ).order_by('scheduled_at', 'created_at')

    attending_ids = set(
        MeetingAttendee.objects.filter(user=user, meeting__in=meetings).values_list('meeting_id', flat=True)
    )
    attendee_map = {
        a.meeting_id: a
        for a in MeetingAttendee.objects.filter(user=user, meeting__in=meetings)
    }
    for m in meetings:
        att = attendee_map.get(m.pk)
        m.notify_before = att.notify_before_minutes if att else 15
        m.repeat_every = att.repeat_every_minutes if att else 1

    return render(request, 'recordings/calendar_settings.html', {
        'meetings': meetings,
        'attending_ids': attending_ids,
        'current_user': user,
    })


@require_http_methods(['GET'])
def api_dadata_suggest(request):
    """Прокси к DaData API подсказок по организациям (токен не светим во фронте)."""
    import requests as req
    token = getattr(settings, 'DADATA_TOKEN', '')
    if not token:
        return JsonResponse({'suggestions': []})
    q = (request.GET.get('q') or '').strip()
    if not q:
        return JsonResponse({'suggestions': []})
    try:
        r = req.post(
            'https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party',
            json={'query': q, 'count': 7, 'status': ['ACTIVE']},
            headers={'Authorization': f'Token {token}', 'Content-Type': 'application/json'},
            timeout=5,
        )
        data = r.json()
        suggestions = []
        for s in data.get('suggestions', []):
            d = s.get('data', {})
            suggestions.append({
                'value': s.get('value', ''),
                'inn': d.get('inn', ''),
                'kpp': d.get('kpp', ''),
                'ogrn': d.get('ogrn', ''),
                'address': (d.get('address') or {}).get('value', ''),
                'type': d.get('type', ''),  # LEGAL / INDIVIDUAL
            })
        return JsonResponse({'suggestions': suggestions})
    except Exception:
        return JsonResponse({'suggestions': []})


def landing(request):
    """Продающий лендинг для magic-kp.ru — регистрация организаций."""
    import random
    import string
    from .telegram_service import get_bot_info

    error = None
    success_code = None
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''
    if not bot_username:
        info = get_bot_info()
        bot_username = info.get('username', '')

    intent = request.GET.get('intent', '') or request.POST.get('intent', '')

    if request.method == 'POST':
        org_name = request.POST.get('org_name', '').strip()
        email = request.POST.get('email', '').strip().lower()

        if not org_name or not email:
            error = 'Заполните все поля.'
        elif SiteUser.objects.filter(email__iexact=email).exists():
            error = 'Пользователь с таким email уже зарегистрирован.'
        elif OrgRegistration.objects.filter(email__iexact=email, status='pending').exists():
            reg = OrgRegistration.objects.filter(email__iexact=email, status='pending').first()
            success_code = reg.verify_code
        else:
            chars = string.ascii_uppercase + string.digits
            code = ''.join(random.choices(chars, k=8))
            while OrgRegistration.objects.filter(verify_code=code).exists():
                code = ''.join(random.choices(chars, k=8))
            OrgRegistration.objects.create(org_name=org_name, email=email, verify_code=code)
            success_code = code

    return render(request, 'recordings/landing.html', {
        'error': error,
        'success_code': success_code,
        'bot_username': bot_username,
        'intent': intent,
    })


@site_login_required
@require_http_methods(['POST'])
def save_speaker_names(request, recording_id):
    import json as _json
    rec = get_object_or_404(Recording, pk=recording_id)
    data = _json.loads(request.body)
    names = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    rec.speaker_names = names
    rec.save(update_fields=['speaker_names'])
    # Save speaker voice profiles + речевые паттерны
    if rec.space:
        services.save_speaker_profiles(
            rec.space, names, rec.speaker_embeddings or {},
            transcription_text=rec.transcription or '',
        )
    return JsonResponse({'ok': True})


@site_login_required
@require_http_methods(['POST'])
def api_tg_link(request):
    """Генерировать код привязки Telegram для любого авторизованного пользователя."""
    import secrets
    import datetime
    user = get_current_user(request)
    code = 'TG' + secrets.token_hex(4).upper()
    user.tg_verify_code = code
    user.tg_verify_expires = timezone.now() + datetime.timedelta(minutes=15)
    user.tg_verified = False
    user.tg_chat_id = None
    user.save(update_fields=['tg_verify_code', 'tg_verify_expires', 'tg_verified', 'tg_chat_id'])
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '')
    bot_link = f'https://t.me/{bot_username}?start={code}' if bot_username else ''
    return JsonResponse({'ok': True, 'code': code, 'bot_link': bot_link})


@site_login_required
@require_http_methods(['POST'])
def send_tg_transcript(request, recording_id):
    """Отправить транскрипцию записи пользователю в Telegram."""
    user = get_current_user(request)
    if not user.tg_chat_id:
        return JsonResponse({'ok': False, 'reason': 'no_tg'}, status=400)
    rec = get_object_or_404(Recording, pk=recording_id)
    if not rec.transcription:
        return JsonResponse({'ok': False, 'reason': 'no_transcription'}, status=400)

    from .telegram_service import send_message as tg_send
    title = rec.ai_title or rec.filename
    # Telegram limit ~4096 chars, split if needed
    header = f'*{title}*\n\n'
    text = rec.transcription
    chunks = []
    limit = 4000
    first = True
    while text:
        prefix = header if first else ''
        chunk = prefix + text[:limit - len(prefix)]
        chunks.append(chunk)
        text = text[limit - len(prefix):]
        first = False

    for chunk in chunks:
        tg_send(user.tg_chat_id, chunk)

    return JsonResponse({'ok': True})


# ─── Маскот: API приёма логов + страница логов ────────────────────────────────

import json as _json_module

@csrf_exempt
@require_http_methods(['POST'])
def api_mascot_log(request):
    """Агент присылает события сюда. Auth: X-Agent-Key = MASTER_API_KEY."""
    key = request.headers.get('X-Agent-Key', '')
    if key != getattr(settings, 'MASTER_API_KEY', ''):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        data = _json_module.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    MascotLog.objects.create(
        room=data.get('room', ''),
        event=data.get('event', 'heard'),
        text=data.get('text', ''),
        speaker=data.get('speaker', ''),
    )
    return JsonResponse({'ok': True})


@site_login_required
def mascot_logs(request):
    room = request.GET.get('room', '')
    qs = MascotLog.objects.all()
    if room:
        qs = qs.filter(room=room)
    logs = qs[:500]
    rooms = MascotLog.objects.values_list('room', flat=True).distinct().order_by('room')
    return render(request, 'recordings/mascot_logs.html', {
        'logs': logs,
        'rooms': rooms,
        'room_filter': room,
    })


@site_login_required
def api_mascot_room_logs(request, room):
    """JSON: логи и задачи Маскота для комнаты (для виджета на странице записи)."""
    from recordings.models import MascotTask
    logs = list(
        MascotLog.objects.filter(room=room)
        .order_by('created_at')
        .values('event', 'text', 'speaker', 'created_at')
    )
    tasks = list(
        MascotTask.objects.filter(room=room)
        .order_by('created_at')
        .values('id', 'title', 'speaker', 'done', 'created_at')
    )
    # Сериализуем datetime
    for e in logs:
        e['created_at'] = e['created_at'].strftime('%H:%M:%S')
    for t in tasks:
        t['created_at'] = t['created_at'].strftime('%H:%M:%S')
    return JsonResponse({'logs': logs, 'tasks': tasks})


@csrf_exempt
@require_http_methods(['POST'])
def api_mascot_task(request):
    """Агент создаёт задачу. Auth: X-Agent-Key."""
    from recordings.models import MascotTask
    key = request.headers.get('X-Agent-Key', '')
    if key != getattr(settings, 'MASTER_API_KEY', ''):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        data = _json_module.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    task = MascotTask.objects.create(
        room=data.get('room', ''),
        title=data.get('title', ''),
        speaker=data.get('speaker', ''),
    )
    return JsonResponse({'ok': True, 'task_id': task.id})


@site_login_required
@require_http_methods(['POST'])
def api_mascot_task_done(request, task_id):
    """Отметить задачу выполненной/невыполненной."""
    from recordings.models import MascotTask
    task = get_object_or_404(MascotTask, pk=task_id)
    task.done = not task.done
    task.save(update_fields=['done'])
    return JsonResponse({'ok': True, 'done': task.done})


@site_login_required
def batch_analyze(request):
    """GET: страница выбора + форма. POST: вызов LLM."""
    from wiki_kb.models import WikiArticle
    import requests as _requests

    if request.method == 'POST':
        import json as _json
        try:
            data = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)

        ids = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
        prompt = data.get('prompt', '').strip()
        if not ids or not prompt:
            return JsonResponse({'error': 'ids and prompt required'}, status=400)

        recs = Recording.objects.filter(pk__in=ids, transcription__isnull=False).exclude(transcription='')
        if not recs.exists():
            return JsonResponse({'error': 'Нет записей с транскрипцией'}, status=400)

        # Собираем все транскрипции
        parts = []
        for rec in recs:
            title = rec.ai_title or rec.filename
            parts.append(f"=== {title} ===\n{rec.transcription}")
        combined = "\n\n".join(parts)

        full_prompt = f"{prompt}\n\n{combined}"

        # Вызываем LLM
        llm_url = getattr(settings, 'LLM_URL', 'https://r-ai.business-pad.com/api/ai_request/')
        llm_auth = getattr(settings, 'LLM_AUTH', 'Basic YXBpX3VzZXI6QXBpVXNlclRlc3QxMjMh')
        llm_referer = getattr(settings, 'LLM_REFERER', 'https://core.business-pad.com/')
        llm_model = getattr(settings, 'LLM_MODEL', 'gpt-4.1-mini')

        try:
            resp = _requests.post(
                llm_url,
                json={
                    'question_to_send': full_prompt,
                    'session_id': 'batch_analyze',
                    'user': 'openai',
                    'log_id': 'log',
                    'model': llm_model,
                },
                headers={
                    'Authorization': llm_auth,
                    'Referer': llm_referer,
                    'Content-Type': 'application/json',
                },
                timeout=120,
            )
            resp.raise_for_status()
            result_data = resp.json()
            messages = result_data.get('messages') or result_data.get('response', '')
            result_text = messages[-1] if isinstance(messages, list) and messages else str(messages)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

        return JsonResponse({'result': result_text})

    # GET
    ids_raw = request.GET.get('ids', '')
    ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]
    recordings = Recording.objects.filter(pk__in=ids)
    wiki_articles = WikiArticle.objects.filter(is_deleted=False).order_by('order', 'title')
    import json as _json
    return render(request, 'recordings/batch_analyze.html', {
        'recordings': recordings,
        'rec_ids_json': _json.dumps(ids),
        'wiki_articles': wiki_articles,
    })


@site_login_required
@require_http_methods(['POST'])
def batch_analyze_place(request):
    """Страница выбора места в вики для результата GPT-анализа."""
    from wiki_kb.models import WikiArticle
    import json as _json

    title = request.POST.get('title', '').strip()
    content = request.POST.get('content', '').strip()
    ids_raw = request.POST.get('ids', '')
    ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]

    if not title or not content:
        return redirect('recordings:index')

    # Строим дерево
    roots = WikiArticle.objects.filter(is_deleted=False, parent__isnull=True).order_by('order', 'title')

    def build_tree(nodes, depth=0):
        result = []
        for node in nodes:
            result.append({
                'article': node,
                'depth': depth,
                'padding_left': f'calc(0.6rem + {depth * 1.4:.1f}rem)',
            })
            result.extend(build_tree(node.get_children(), depth + 1))
        return result

    wiki_tree = build_tree(roots)

    return render(request, 'recordings/batch_analyze_place.html', {
        'title': title,
        'content': content,
        'ids_json': _json.dumps(ids),
        'wiki_tree': wiki_tree,
    })


@site_login_required
@require_http_methods(['POST'])
def batch_analyze_create_wiki(request):
    """Создать вики-статью с результатом GPT."""
    from wiki_kb.models import WikiArticle, WikiRevision
    import json as _json
    from django.utils.text import slugify

    # Принимаем и form-data (от batch_analyze_place), и JSON (legacy)
    content_type = request.content_type or ''
    if 'application/json' in content_type:
        try:
            data = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)
        title = data.get('title', '').strip()
        content = data.get('content', '').strip()
        parent_id = data.get('parent_id', '')
        ids = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
    else:
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        parent_id = request.POST.get('parent_id', '')
        ids_raw = request.POST.get('ids', '')
        ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]

    if not title or not content:
        return redirect('recordings:index')

    parent = None
    if parent_id:
        try:
            parent = WikiArticle.objects.get(pk=int(parent_id))
        except (WikiArticle.DoesNotExist, ValueError):
            pass

    # Уникальный slug
    base_slug = slugify(title) or 'batch-analysis'
    slug = base_slug
    counter = 1
    while WikiArticle.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Привязываем записи
    recordings = Recording.objects.filter(pk__in=ids)

    article = WikiArticle.objects.create(
        title=title,
        slug=slug,
        content=content,
        parent=parent,
    )
    article.recordings.set(recordings)

    WikiRevision.objects.create(
        article=article,
        content=content,
        comment='Создано через пакетный анализ GPT',
    )

    url = f"/kb/{slug}/"
    # Если JSON-запрос — вернуть JSON, если form — редирект
    content_type = request.content_type or ''
    if 'application/json' in content_type:
        return JsonResponse({'url': url})
    return redirect(url)


# ── Системные настройки / GPU-режим ──────────────────────────────────────────

def _write_ocr_gpu_flag(enabled: bool):
    """Записываем файл-флаг на shared volume, OCR контейнер его читает."""
    import os as _os
    flag_path = _os.path.join(getattr(settings, 'MEDIA_ROOT', '/app/data/media'), '..', 'ocr_gpu_mode')
    flag_path = _os.path.normpath(flag_path)
    try:
        with open(flag_path, 'w') as f:
            f.write('1' if enabled else '0')
    except Exception:
        pass


@site_login_required
def system_config(request):
    """Страница системных настроек (только для BP-пользователей)."""
    from django.conf import settings as django_settings
    user = get_current_user(request)
    bp_slug = getattr(django_settings, 'BP_SPACE_SLUG', 'org-bp')
    if not user or not user.space or user.space.slug != bp_slug:
        return redirect('recordings:index')

    if request.method == 'POST':
        key = request.POST.get('key', '').strip()
        value = request.POST.get('value', '').strip()
        if key == 'reset_stuck':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .models import Recording as _Rec
            from .queue_services import enqueue_transcribe as _enqueue
            stuck = list(_Rec.objects.filter(status=_Rec.Status.TRANSCRIBING))
            for r in stuck:
                r.status = _Rec.Status.STABLE
                r.transcription_progress = 0
                r.transcription_stage = ''
                r.save(update_fields=['status', 'transcription_progress', 'transcription_stage'])
                _enqueue(r, priority=1)
            messages.success(request, f'Сброшено {len(stuck)} зависших → STABLE, поставлены в приоритет.')
        elif key == 'clear_queues':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .models import TranscribeQueue as _TQ, EmbeddingQueue as _EQ
            tq = _TQ.objects.count()
            eq = _EQ.objects.count()
            _TQ.objects.all().delete()
            _EQ.objects.all().delete()
            messages.success(request, f'Очереди очищены: транскрибация {tq}, эмбеддинги {eq}.')
        elif key == 'enqueue_missing':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .models import TranscribeQueue as _TQ
            from .queue_services import enqueue_transcribe as _enqueue
            from django.db.models import Q as _Q
            already_queued = set(_TQ.objects.values_list('recording_id', flat=True))
            recs = list(
                Recording.objects
                .filter(s3_key__isnull=False)
                .exclude(s3_key='')
                .exclude(status__in=[Recording.Status.PENDING, Recording.Status.DONE, Recording.Status.TRANSCRIBING])
                .exclude(_Q(transcription__isnull=False) & ~_Q(transcription=''))
                .order_by('-created_at')
            )
            added = 0
            for r in recs:
                if r.pk not in already_queued:
                    _enqueue(r, priority=0)
                    added += 1
            messages.success(request, f'Добавлено в очередь: {added} записей (без уже стоящих).')
        elif key == 'restart_one':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .queue_services import enqueue_transcribe as _enqueue
            rec_id = request.POST.get('recording_id', '')
            try:
                r = Recording.objects.get(pk=int(rec_id))
            except (Recording.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Запись не найдена.')
                return redirect('recordings:system_config')
            if r.status == Recording.Status.TRANSCRIBING:
                r.status = Recording.Status.STABLE
                r.transcription_progress = 0
                r.transcription_stage = ''
                r.save(update_fields=['status', 'transcription_progress', 'transcription_stage'])
            _enqueue(r, priority=1)
            messages.success(request, f'Запись «{r.filename}» поставлена в приоритетную очередь.')
        elif key == 'reprocess_all':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .models import TranscribeQueue as _TQ, EmbeddingQueue as _EQ
            from .queue_services import enqueue_transcribe as _enqueue
            from django.db.models import Q as _Q
            # 1. Запомнить ID записей, которые уже стояли в очереди
            already_queued_ids = set(_TQ.objects.values_list('recording_id', flat=True))
            # 2. Очистить все очереди
            _TQ.objects.all().delete()
            _EQ.objects.all().delete()
            # 3. Сбросить зависшие TRANSCRIBING → STABLE
            Recording.objects.filter(status=Recording.Status.TRANSCRIBING).update(
                status=Recording.Status.STABLE,
                transcription_progress=0,
                transcription_stage='',
            )
            # 4. Ставим в очередь:
            #    - те что уже стояли в очереди (already_queued_ids) — сохраняем их место
            #    - те у которых нет транскрипции И статус не DONE и не PENDING
            #    Исключаем: PENDING (файл нестабилен), DONE (уже обработаны), с готовой транскрипцией
            has_transcription = _Q(transcription__isnull=False) & ~_Q(transcription='')
            recs = list(
                Recording.objects
                .filter(s3_key__isnull=False)
                .exclude(s3_key='')
                .filter(
                    _Q(pk__in=already_queued_ids) |
                    (
                        ~_Q(status__in=[Recording.Status.PENDING, Recording.Status.DONE]) &
                        ~has_transcription
                    )
                )
                .exclude(has_transcription)
                .order_by('-created_at')
            )
            for r in recs:
                r.transcription_progress = 0
                r.transcription_stage = ''
                r.status = Recording.Status.STABLE
                r.save(update_fields=['status', 'transcription_progress', 'transcription_stage'])
                _enqueue(r, priority=0)
            messages.success(request, f'Поставлено в очередь {len(recs)} записей (без уже обработанных). Поллер обработает их последовательно.')
        elif key == 'default_transcription_model':
            allowed_models = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
            if value in allowed_models:
                SystemConfig.set('default_transcription_model', value)
                messages.success(request, f'Модель по умолчанию: {value}')
            else:
                messages.error(request, 'Неизвестная модель.')
        elif key == 'bulk_enqueue_selected':
            if request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            from .queue_services import enqueue_transcribe as _enqueue
            model = request.POST.get('bulk_model', 'small')
            allowed_models = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
            if model not in allowed_models:
                model = 'small'
            rec_ids = [int(x) for x in request.POST.getlist('rec_ids') if x.isdigit()]
            recs = list(Recording.objects.filter(pk__in=rec_ids))
            for r in recs:
                r.transcription_quality = model
                r.transcription_progress = 0
                r.transcription_stage = ''
                if r.status == Recording.Status.TRANSCRIBING:
                    r.status = Recording.Status.STABLE
                r.save(update_fields=['transcription_quality', 'transcription_progress', 'transcription_stage', 'status'])
                _enqueue(r, priority=1)
            messages.success(request, f'Поставлено в очередь: {len(recs)} записей, модель «{model}».')
        elif key == 'llm_provider':
            # Выбор провайдера LLM — без пароля (не деструктивно)
            allowed = ('gonka', 'openai', 'grok')
            if value in allowed:
                SystemConfig.set('llm_provider', value)
                messages.success(request, f'LLM провайдер переключён на {value}.')
            else:
                messages.error(request, 'Неизвестный провайдер.')
        elif key:
            if key == 'ocr_gpu_mode' and request.POST.get('confirm_password') != '11':
                messages.error(request, 'Неверный пароль.')
                return redirect('recordings:system_config')
            SystemConfig.set(key, value)
            if key == 'ocr_gpu_mode':
                _write_ocr_gpu_flag(value == '1')
        return redirect('recordings:system_config')

    ocr_gpu_mode = SystemConfig.get('ocr_gpu_mode', '0') == '1'

    from .models import TranscribeQueue, EmbeddingQueue
    from django.db.models import Q as _Q
    transcribing_now = Recording.objects.filter(status=Recording.Status.TRANSCRIBING).order_by('-updated_at')
    transcribe_queue = TranscribeQueue.objects.select_related('recording').order_by('-priority', 'created_at')
    embedding_queue = EmbeddingQueue.objects.select_related('recording').order_by('created_at')

    # Необработанные записи: есть s3_key, нет транскрипции, статус не PENDING
    unprocessed = Recording.objects.filter(
        s3_key__isnull=False
    ).exclude(s3_key='').exclude(
        status=Recording.Status.PENDING
    ).filter(
        _Q(transcription__isnull=True) | _Q(transcription='')
    ).select_related('space').order_by('-created_at')[:200]

    default_model = SystemConfig.get('default_transcription_model', 'small')

    # Читаем livekit.yaml
    import yaml as _yaml
    lk_config = None
    lk_yaml_error = None
    import os as _os2
    lk_yaml_path = _os2.path.join(str(django_settings.BASE_DIR), 'livekit.yaml')
    try:
        with open(lk_yaml_path) as f:
            lk_config = _yaml.safe_load(f)
    except FileNotFoundError:
        lk_yaml_error = f'Файл не найден: {lk_yaml_path}'
    except Exception as e:
        lk_yaml_error = str(e)

    def _mask(val, show=4):
        """Маскирует чувствительный ключ: первые show символов + ••••••"""
        if not val:
            return ''
        return val[:show] + '••••••' if len(val) > show else '••••••'

    # Маскируем ключи из YAML чтобы не попали в HTML
    if lk_config and isinstance(lk_config.get('keys'), dict):
        lk_config = dict(lk_config)
        lk_config['keys'] = {k: _mask(str(v)) for k, v in lk_config['keys'].items()}

    import os as _os3
    llm_provider = SystemConfig.get('llm_provider', '')
    gonka_key_set = bool(_os3.environ.get('GONKA_PRIVATE_KEY') or SystemConfig.get('gonka_private_key', ''))
    openai_key_set = bool(SystemConfig.get('openai_api_key', ''))
    grok_key_set = bool(SystemConfig.get('grok_api_key', ''))
    # Эффективный провайдер (с учётом автовыбора)
    if llm_provider:
        effective_provider = llm_provider
    elif gonka_key_set:
        effective_provider = 'gonka'
    elif grok_key_set:
        effective_provider = 'grok'
    elif openai_key_set:
        effective_provider = 'openai'
    else:
        effective_provider = '—'

    transcription_models = [
        ('tiny',           'Tiny (очень быстро, хуже качество)'),
        ('base',           'Base (быстро)'),
        ('small',          'Small (баланс) — по умолчанию'),
        ('medium',         'Medium (хорошее качество)'),
        ('large-v3',       'Large-v3 (наилучшее)'),
        ('large-v3-turbo', 'Large-v3-turbo (быстро + качество)'),
    ]

    return render(request, 'recordings/system_config.html', {
        'ocr_gpu_mode': ocr_gpu_mode,
        'unprocessed': unprocessed,
        'default_model': default_model,
        'models': transcription_models,
        'lk_config': lk_config,
        'lk_yaml_error': lk_yaml_error,
        'lk_url': getattr(django_settings, 'LIVEKIT_URL', ''),
        'lk_api_key': _mask(getattr(django_settings, 'LIVEKIT_API_KEY', '')),
        'transcribing_now': transcribing_now,
        'transcribe_queue': transcribe_queue,
        'embedding_queue': embedding_queue,
        'llm_provider': llm_provider,
        'effective_provider': effective_provider,
        'gonka_key_set': gonka_key_set,
        'openai_key_set': openai_key_set,
        'grok_key_set': grok_key_set,
    })


@csrf_exempt
def api_system_config(request):
    """Внутренний API для чтения настроек (только GET, с хоста)."""
    key = request.GET.get('key', '')
    if not key:
        return JsonResponse({'error': 'key required'}, status=400)
    return JsonResponse({'key': key, 'value': SystemConfig.get(key, '')})


# ── Встречи (LiveKit Meeting Rooms) ──────────────────────────────────────────

def _generate_livekit_token(room_name: str, identity: str, display_name: str) -> str:
    """Генерирует JWT токен для подключения к LiveKit комнате."""
    import jwt
    import time
    api_key = getattr(settings, 'LIVEKIT_API_KEY', '')
    api_secret = getattr(settings, 'LIVEKIT_API_SECRET', '')
    now = int(time.time())
    payload = {
        'iss': api_key,
        'sub': identity,
        'nbf': now,
        'exp': now + 3600 * 4,
        'name': display_name,
        'video': {
            'room': room_name,
            'roomJoin': True,
            'canPublish': True,
            'canSubscribe': True,
        },
    }
    return jwt.encode(payload, api_secret, algorithm='HS256')


@site_login_required
def meetings_page(request):
    """Список встреч пространства."""
    user = get_current_user(request)
    qs = MeetingRoom.objects.filter(space=user.space, ended_at__isnull=True) if user and user.space else MeetingRoom.objects.none()
    recurring_busy = list(RecurringBusyTime.objects.filter(owner=user, is_active=True).order_by('start_time')) if user else []
    return render(request, 'recordings/meetings.html', {
        'meetings': qs,
        'recurring_busy': recurring_busy,
    })


@site_login_required
def meetings_export_csv(request):
    """Экспорт встреч в CSV."""
    import csv
    from django.http import HttpResponse
    user = get_current_user(request)
    qs = MeetingRoom.objects.filter(space=user.space).order_by('-created_at') if user and user.space else MeetingRoom.objects.none()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="meetings.csv"'
    response.write('\ufeff')  # BOM for Excel
    writer = csv.writer(response)
    writer.writerow(['Название', 'ID комнаты', 'Начало', 'Конец', 'Маскот', 'Создал', 'Завершена', 'Ссылка'])
    for m in qs:
        writer.writerow([
            m.title,
            m.room_name,
            m.scheduled_at.strftime('%d.%m.%Y %H:%M') if m.scheduled_at else '',
            m.ended_at.strftime('%d.%m.%Y %H:%M') if m.ended_at else '',
            'Да' if m.with_mascot else 'Нет',
            m.created_by.email if m.created_by else '',
            m.ended_at.strftime('%d.%m.%Y %H:%M') if m.ended_at else '',
            f'https://meet.business-pad.com/rooms/{m.room_name}',
        ])
    return response


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def meetings_import_csv(request):
    """Импорт встреч из CSV. Создаёт MeetingRoom записи без LiveKit комнаты."""
    import csv
    import io
    user = get_current_user(request)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'file required'}, status=400)
    try:
        content = f.read().decode('utf-8-sig')
        from datetime import datetime as _dt
        import uuid as _uuid2

        def _parse_dt(s):
            """Parse '25.03.2026 10:00' or '2026-03-25 10:00' or '2026-03-25T10:00'."""
            if not s:
                return None
            s = s.strip()
            for fmt in ('%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%d.%m.%Y', '%Y-%m-%d'):
                try:
                    return timezone.make_aware(_dt.strptime(s, fmt))
                except ValueError:
                    continue
            return None

        reader = csv.DictReader(io.StringIO(content))
        created = 0
        skipped = 0
        for row in reader:
            title = (row.get('Название') or row.get('title') or '').strip()
            room_name = (row.get('ID комнаты') or row.get('room_name') or '').strip()
            scheduled_str = (row.get('Начало') or row.get('scheduled_at') or row.get('start') or '').strip()
            ended_str = (row.get('Конец') or row.get('ended_at') or row.get('end') or '').strip()
            if not title or title.startswith('#'):
                skipped += 1
                continue
            # Validate room_name: Zoom/Meet links and special chars are invalid LiveKit room names
            import re as _re
            if not room_name or _re.search(r'[?/\\]', room_name) or len(room_name) > 64:
                room_name = _uuid2.uuid4().hex[:12]
            # If room_name taken, generate new unique one (don't skip)
            if MeetingRoom.objects.filter(room_name=room_name).exists():
                room_name = _uuid2.uuid4().hex[:12]
                while MeetingRoom.objects.filter(room_name=room_name).exists():
                    room_name = _uuid2.uuid4().hex[:12]
            scheduled_at = _parse_dt(scheduled_str)
            ended_at = _parse_dt(ended_str)
            MeetingRoom.objects.create(
                room_name=room_name,
                title=title,
                with_mascot=False,
                created_by=user,
                space=user.space if user else None,
                scheduled_at=scheduled_at,
                ended_at=ended_at,
            )
            created += 1
        return JsonResponse({'ok': True, 'created': created, 'skipped': skipped})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@site_login_required
@require_http_methods(['POST'])
@csrf_exempt
def create_meeting(request):
    """Создать встречу. POST JSON: {title, with_mascot}"""
    import uuid
    import json as _json
    user = get_current_user(request)
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    title = data.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'title required'}, status=400)
    with_mascot = bool(data.get('with_mascot', False))
    scheduled_at_str = data.get('scheduled_at', '').strip() if data.get('scheduled_at') else ''
    scheduled_at = None
    if scheduled_at_str:
        from datetime import datetime as _dt
        for fmt in ('%d.%m.%Y %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
            try:
                scheduled_at = timezone.make_aware(_dt.strptime(scheduled_at_str, fmt))
                break
            except ValueError:
                continue
    from django.utils.text import slugify
    base_slug = slugify(title) or uuid.uuid4().hex[:12]
    room_name = base_slug
    suffix = 1
    while MeetingRoom.objects.filter(room_name=room_name).exists():
        room_name = f'{base_slug}-{suffix}'
        suffix += 1
    ended_at_str = data.get('ended_at', '').strip() if data.get('ended_at') else ''
    ended_at = None
    if ended_at_str:
        for fmt in ('%d.%m.%Y %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
            try:
                ended_at = timezone.make_aware(_dt.strptime(ended_at_str, fmt))
                break
            except ValueError:
                continue
    meeting = MeetingRoom.objects.create(
        room_name=room_name,
        title=title,
        with_mascot=with_mascot,
        created_by=user,
        space=user.space if user else None,
        scheduled_at=scheduled_at,
        ended_at=ended_at,
    )
    tg_users = []
    if user and user.space:
        qs = SiteUser.objects.filter(space=user.space, tg_chat_id__isnull=False).exclude(pk=user.pk)
        tg_users = [{'id': u.pk, 'email': u.email} for u in qs]
    join_url = f'https://meet.business-pad.com/rooms/{room_name}'
    return JsonResponse({
        'room_name': room_name,
        'title': title,
        'with_mascot': with_mascot,
        'join_url': join_url,
        'tg_users': tg_users,
    })


@site_login_required
def meeting_room(request, room_name):
    """Страница встречи (LiveKit embed)."""
    meeting = get_object_or_404(MeetingRoom, room_name=room_name)
    user = get_current_user(request)
    lk_url = getattr(settings, 'LIVEKIT_URL', '')
    token = _generate_livekit_token(room_name, str(user.pk), user.email)
    return render(request, 'recordings/meeting_room.html', {
        'meeting': meeting,
        'lk_url': lk_url,
        'token': token,
    })


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def meeting_invite(request, room_name):
    """Отправить TG-приглашения участникам встречи. POST JSON: {user_ids: [...]}"""
    import json as _json
    from . import telegram_service
    meeting = get_object_or_404(MeetingRoom, room_name=room_name)
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    user_ids = [int(i) for i in data.get('user_ids', []) if str(i).isdigit()]
    comment = data.get('comment', '').strip() or None
    join_url = meeting.join_url or f'https://meet.business-pad.com/rooms/{room_name}'
    users = SiteUser.objects.filter(pk__in=user_ids)
    sent = 0
    for u in users:
        # Create attendance record so meeting appears as "theirs" and they get 15-min reminder
        MeetingAttendee.objects.get_or_create(
            user=u, meeting=meeting,
            defaults={'notify_before_minutes': 15, 'repeat_every_minutes': 1},
        )
        if u.tg_chat_id:
            telegram_service.send_meeting_invite(
                u.tg_chat_id, meeting.title, join_url,
                comment=comment,
                scheduled_at=meeting.scheduled_at,
                ended_at=meeting.ended_at,
            )
            sent += 1
    return JsonResponse({'ok': True, 'sent': sent})


@site_login_required
def api_space_members(request):
    """JSON список участников пространства (для приглашения на встречу)."""
    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'members': []})
    room_name = request.GET.get('room')
    invited_ids = set()
    if room_name:
        try:
            meeting = MeetingRoom.objects.get(room_name=room_name)
            invited_ids = set(MeetingAttendee.objects.filter(meeting=meeting).values_list('user_id', flat=True))
        except MeetingRoom.DoesNotExist:
            pass
    members = SiteUser.objects.filter(space=user.space).exclude(pk=user.pk)
    return JsonResponse({'members': [
        {'id': m.pk, 'email': m.email, 'name': m.display_name or m.email, 'has_tg': bool(m.tg_chat_id), 'invited': m.pk in invited_ids}
        for m in members
    ]})


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def end_meeting(request, room_name):
    """Завершить встречу (выставить ended_at)."""
    from django.utils import timezone
    meeting = get_object_or_404(MeetingRoom, room_name=room_name)
    meeting.ended_at = timezone.now()
    meeting.save(update_fields=['ended_at'])
    return JsonResponse({'ok': True})


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def delete_meeting(request, room_name):
    """Удалить встречу. Если есть записи — отвязать от пространства (мягкое удаление), иначе удалить совсем."""
    meeting = get_object_or_404(MeetingRoom, room_name=room_name)
    has_recordings = Recording.objects.filter(filename__contains=room_name).exists()
    if has_recordings:
        # Soft delete: disassociate from space so it disappears from calendar
        meeting.space = None
        meeting.save(update_fields=['space'])
    else:
        meeting.delete()
    return JsonResponse({'ok': True})


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def edit_meeting(request, room_name):
    """Переименовать/перенести встречу. POST JSON: {title, scheduled_at}"""
    import json as _json
    user = get_current_user(request)
    meeting, _ = MeetingRoom.objects.get_or_create(
        room_name=room_name,
        defaults={
            'title': room_name,
            'space': user.space if user else None,
            'created_by': user,
        },
    )
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    title = data.get('title', '').strip()
    scheduled_str = data.get('scheduled_at', '').strip()
    update_fields = []
    if title:
        meeting.title = title
        update_fields.append('title')
    if scheduled_str:
        from datetime import datetime as _dt
        for fmt in ('%Y-%m-%dT%H:%M', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M'):
            try:
                dt = timezone.make_aware(_dt.strptime(scheduled_str, fmt))
                meeting.scheduled_at = dt
                update_fields.append('scheduled_at')
                break
            except ValueError:
                continue
    elif 'scheduled_at' in data and data['scheduled_at'] == '':
        meeting.scheduled_at = None
        update_fields.append('scheduled_at')
    join_url = data.get('join_url', '').strip()
    meeting.join_url = join_url or None
    update_fields.append('join_url')
    if 'repeat' in data:
        repeat = data.get('repeat', '')
        if repeat in dict(MeetingRoom.REPEAT_CHOICES):
            meeting.repeat = repeat
            update_fields.append('repeat')
    if update_fields:
        meeting.save(update_fields=update_fields)
    return JsonResponse({'ok': True, 'title': meeting.title,
                         'scheduled_at': meeting.scheduled_at.isoformat() if meeting.scheduled_at else None,
                         'join_url': meeting.join_url or '',
                         'repeat': meeting.repeat})


@require_http_methods(['GET'])
def meetings_csv_sample(request):
    """Скачать пример CSV файла для импорта встреч."""
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="meetings_sample.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['Название', 'ID комнаты', 'Начало', 'Конец'])
    writer.writerow(['# Формат даты: дд.мм.гггг чч:мм  Московское время', '', '', ''])
    writer.writerow(['# ID комнаты — необязателен. Оставьте пустым — система создаст ID сама.', '', '', ''])
    writer.writerow(['# Для повторяющихся встреч оставляйте ID пустым (каждая строка = отдельная встреча)', '', '', ''])
    writer.writerow(['Еженедельная планёрка', '', '25.03.2026 10:00', '25.03.2026 11:00'])
    writer.writerow(['Еженедельная планёрка', '', '01.04.2026 10:00', '01.04.2026 11:00'])
    writer.writerow(['Созвон с клиентом', 'klient-call-1', '26.03.2026 14:30', '26.03.2026 15:00'])
    writer.writerow(['Ретроспектива', '', '28.03.2026 16:00', '28.03.2026 17:30'])
    return response


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def invite_mascot(request, room_name):
    """Включить маскота в существующей встрече. Создаёт запись в БД если её нет."""
    user = get_current_user(request)
    meeting, _ = MeetingRoom.objects.get_or_create(
        room_name=room_name,
        defaults={
            'title': room_name,
            'with_mascot': False,
            'created_by': user,
            'space': user.space if user else None,
        }
    )
    meeting.with_mascot = True
    meeting.save(update_fields=['with_mascot'])
    return JsonResponse({'ok': True})


@site_login_required
def api_livekit_rooms(request):
    """Данные о живых комнатах LiveKit + инфо из БД."""
    import asyncio
    from django.utils import timezone as tz

    lk_url = getattr(settings, 'LIVEKIT_URL', '').replace('ws://', 'http://').replace('wss://', 'https://')
    lk_key = getattr(settings, 'LIVEKIT_API_KEY', '')
    lk_secret = getattr(settings, 'LIVEKIT_API_SECRET', '')

    if not lk_url or not lk_key or not lk_secret:
        return JsonResponse({'rooms': [], 'error': 'LiveKit не настроен'})

    async def _fetch():
        try:
            from livekit import api as lkapi
            async with lkapi.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
                rooms_resp = await lk.room.list_rooms(lkapi.ListRoomsRequest())
                result = []
                for room in rooms_resp.rooms:
                    participants = []
                    try:
                        p_resp = await lk.room.list_participants(
                            lkapi.ListParticipantsRequest(room=room.name)
                        )
                        for p in p_resp.participants:
                            participants.append({
                                'identity': p.identity,
                                'name': p.name or p.identity,
                                'is_publisher': p.num_tracks > 0,
                                'tracks': p.num_tracks,
                                'joined_at': p.joined_at,
                            })
                    except Exception:
                        pass
                    result.append({
                        'name': room.name,
                        'num_participants': room.num_participants,
                        'num_publishers': room.num_publishers,
                        'creation_time': room.creation_time,
                        'active_recording': room.active_recording,
                        'metadata': room.metadata or '',
                        'participants': participants,
                    })
                return result
        except Exception as e:
            return {'error': str(e)}

    try:
        raw = asyncio.run(_fetch())
    except RuntimeError:
        # Если event loop уже запущен (редко в Django sync), fallback
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            raw = ex.submit(asyncio.run, _fetch()).result()

    if isinstance(raw, dict) and 'error' in raw:
        return JsonResponse({'rooms': [], 'error': raw['error']})

    # Обогащаем данными из БД
    user = get_current_user(request)
    db_rooms = {}
    if user and user.space:
        for m in MeetingRoom.objects.filter(space=user.space):
            db_rooms[m.room_name] = {
                'id': m.pk,
                'title': m.title,
                'with_mascot': m.with_mascot,
                'ended_at': m.ended_at.isoformat() if m.ended_at else None,
                'scheduled_at': m.scheduled_at.isoformat() if m.scheduled_at else None,
                'join_url': m.join_url or '',
                'repeat': m.repeat or '',
                'created_by': m.created_by.email if m.created_by else '',
            }

    now_ts = int(tz.now().timestamp())
    rooms_out = []
    for r in raw:
        db = db_rooms.get(r['name'], {})
        duration_sec = now_ts - r['creation_time'] if r['creation_time'] else 0
        rooms_out.append({
            **r,
            'duration_sec': duration_sec,
            'db': db,
        })

    return JsonResponse({'rooms': rooms_out})


@site_login_required
def api_calendar_events(request):
    """JSON события для календаря встреч. Группирует записи по room_code."""
    import re
    from collections import defaultdict
    from datetime import timedelta
    from django.utils.dateparse import parse_datetime
    from zoneinfo import ZoneInfo
    _MSK = ZoneInfo('Europe/Moscow')

    def _iso_msk(dt):
        """Конвертирует datetime в МСК и возвращает ISO без offset (FullCalendar интерпретирует как localTime)."""
        if dt is None:
            return None
        return dt.astimezone(_MSK).strftime('%Y-%m-%dT%H:%M:%S')

    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'events': []})

    mine_only = request.GET.get('mine') == '1'
    my_meeting_ids = set(MeetingAttendee.objects.filter(user=user).values_list('meeting_id', flat=True))
    rooms_qs = MeetingRoom.objects.filter(space=user.space).select_related('created_by')
    if mine_only:
        rooms_qs = rooms_qs.filter(pk__in=my_meeting_ids)
    rooms = list(rooms_qs)
    room_map = {r.room_name: r for r in rooms}

    all_recs = list(Recording.objects.filter(space=user.space).only(
        'pk', 'filename', 'ai_title', 'status'
    ))

    fn_pattern = re.compile(
        r'^(\d{4}-\d{2}-\d{2}T[\d.:]+Z)-(.+?)(?:-org-[a-z0-9-]+)?\.mp3$',
        re.IGNORECASE,
    )

    room_rec_map = defaultdict(list)  # room_code → [(dt, recording)]
    for rec in all_recs:
        m = fn_pattern.match(rec.filename)
        if not m:
            continue
        ts_str, room_code = m.group(1), m.group(2)
        ts = parse_datetime(ts_str)
        if ts is None:
            continue
        room_rec_map[room_code].append((ts, rec))

    events = []

    def _is_all_day(dt):
        """True если время ровно полночь — признак all-day события из Google Calendar."""
        return dt is not None and dt.hour == 0 and dt.minute == 0 and dt.second == 0

    # 1. Events from MeetingRoom entries
    for room in rooms:
        recs = sorted(room_rec_map.get(room.room_name, []), key=lambda x: x[0])
        start = room.scheduled_at or (recs[0][0] if recs else room.created_at)
        # start гарантированно не None (created_at всегда есть)
        end = room.ended_at

        # Для all-day событий из Google Calendar оба времени — полночь
        all_day = _is_all_day(room.scheduled_at) and _is_all_day(room.ended_at)

        if not end:
            # Берём только записи, которые начались ПОСЛЕ start (чтобы не получить end < start)
            recs_after_start = [r for r in recs if r[0] >= start]
            if recs_after_start:
                end = recs_after_start[-1][0] + timedelta(minutes=5)
            elif all_day:
                end = start + timedelta(days=1)
            else:
                end = start + timedelta(minutes=60)

        # Гарантируем end > start (защита от ended_at < scheduled_at в БД)
        if end <= start:
            end = start + timedelta(minutes=60)

        is_mine = room.pk in my_meeting_ids
        color = '#16a34a' if not room.ended_at else ('#7c3aed' if is_mine else '#2563eb')

        ev = {
            'id': f'room-{room.pk}',
            'title': room.title or room.room_name or '(без названия)',
            'color': color,
            'extendedProps': {
                'room_name': room.room_name,
                'is_mine': is_mine,
                'room_url': f'/meetings/{room.room_name}/',
                'is_active': room.ended_at is None,
                'join_url': room.join_url or '',
                'scheduled_at': room.scheduled_at.isoformat() if room.scheduled_at else None,
                'repeat': room.repeat or '',
                'created_by': room.created_by.email if room.created_by else '',
                'recordings': [
                    {
                        'id': r.pk,
                        'title': r.ai_title or r.filename or '(без названия)',
                        'url': f'/r/{r.pk}/',
                        'status': r.status,
                        'ts': ts.isoformat(),
                    }
                    for ts, r in recs
                ],
            },
        }
        if all_day:
            # FullCalendar: all-day → передаём дату без времени
            ev['start'] = start.astimezone(_MSK).strftime('%Y-%m-%d')
            ev['end'] = end.astimezone(_MSK).strftime('%Y-%m-%d')
            ev['allDay'] = True
        else:
            ev['start'] = _iso_msk(start)
            ev['end'] = _iso_msk(end)
        events.append(ev)

        # Генерируем повторяющиеся события (weekly) на 8 недель вперёд
        if room.repeat == 'weekly' and not all_day and room.scheduled_at:
            duration = end - start
            for week in range(1, 9):
                rep_start = start + timedelta(weeks=week)
                rep_end = rep_start + duration
                rep_ev = dict(ev)
                rep_ev['id'] = f'room-{room.pk}-w{week}'
                rep_ev['start'] = _iso_msk(rep_start)
                rep_ev['end'] = _iso_msk(rep_end)
                rep_ev['extendedProps'] = dict(ev['extendedProps'])
                rep_ev['extendedProps']['scheduled_at'] = rep_start.isoformat()
                events.append(rep_ev)

    # 2. Events from recordings without a MeetingRoom entry
    for room_code, rec_list in room_rec_map.items():
        if room_code in room_map:
            continue
        sorted_recs = sorted(rec_list, key=lambda x: x[0])

        # Split into sessions: gap > 2h → new session
        sessions = []
        cur = [sorted_recs[0]]
        for item in sorted_recs[1:]:
            if item[0] - cur[-1][0] > timedelta(hours=2):
                sessions.append(cur)
                cur = [item]
            else:
                cur.append(item)
        sessions.append(cur)

        for i, session in enumerate(sessions):
            start = session[0][0]
            end = session[-1][0] + timedelta(minutes=5)
            events.append({
                'id': f'anon-{room_code}-{i}',
                'title': room_code,
                'start': _iso_msk(start),
                'end': _iso_msk(end),
                'color': '#64748b',
                'extendedProps': {
                    'room_name': room_code,
                    'room_url': None,
                    'is_active': False,
                    'created_by': '',
                    'recordings': [
                        {
                            'id': r.pk,
                            'title': r.ai_title or r.filename,
                            'url': f'/r/{r.pk}/',
                            'status': r.status,
                            'ts': ts.isoformat(),
                        }
                        for ts, r in session
                    ],
                },
            })

    return JsonResponse({'events': events})


@site_login_required
@site_login_required
def recurring_busy_times(request):
    """CRUD для повторяющихся занятых интервалов (API: GET list, POST create, DELETE /<pk>/)."""
    import json as _json
    from .models import RecurringBusyTime
    user = get_current_user(request)
    if request.method == 'POST':
        try:
            data = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)
        title = (data.get('title') or '').strip()
        start = (data.get('start_time') or '').strip()
        end = (data.get('end_time') or '').strip()
        repeat = data.get('repeat', 'daily')
        if not title or not start or not end:
            return JsonResponse({'error': 'title, start_time, end_time required'}, status=400)
        if repeat not in dict(RecurringBusyTime.REPEAT_CHOICES):
            repeat = 'daily'
        obj = RecurringBusyTime.objects.create(
            owner=user, title=title, start_time=start, end_time=end, repeat=repeat,
        )
        return JsonResponse({'ok': True, 'id': obj.pk, 'title': obj.title,
                             'start_time': start, 'end_time': end, 'repeat': obj.repeat,
                             'repeat_label': obj.get_repeat_display()})
    # GET
    items = list(RecurringBusyTime.objects.filter(owner=user, is_active=True).order_by('start_time'))
    return JsonResponse({'items': [
        {'id': o.pk, 'title': o.title,
         'start_time': o.start_time.strftime('%H:%M'),
         'end_time': o.end_time.strftime('%H:%M'),
         'repeat': o.repeat, 'repeat_label': o.get_repeat_display()}
        for o in items
    ]})


@site_login_required
@require_http_methods(['POST'])
def delete_recurring_busy_time(request, pk):
    from .models import RecurringBusyTime
    user = get_current_user(request)
    try:
        obj = RecurringBusyTime.objects.get(pk=pk, owner=user)
        obj.delete()
        return JsonResponse({'ok': True})
    except RecurringBusyTime.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)


@csrf_exempt
@require_http_methods(['POST'])
def share_day_create(request):
    """Создать или обновить ссылку-поделиться на свободные слоты дня.
    POST JSON: {date, busy_slots, slot_duration_minutes, day_start, day_end, is_permanent}
    is_permanent=true — ссылка всегда показывает завтрашний день.
    """
    import json as _json
    from datetime import date as _date
    user = get_current_user(request)
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    is_permanent = bool(data.get('is_permanent', False))
    busy_slots = data.get('busy_slots', [])
    slot_min = int(data.get('slot_duration_minutes', 30))
    day_start = data.get('day_start', '09:00')
    day_end = data.get('day_end', '18:00')

    if is_permanent:
        # Upsert: one permanent link per owner
        link, _ = DayShareLink.objects.update_or_create(
            owner=user,
            is_permanent=True,
            defaults={
                'date': None,
                'busy_slots': busy_slots,
                'slot_duration_minutes': slot_min,
                'day_start': day_start,
                'day_end': day_end,
            },
        )
    else:
        date_str = data.get('date', '')
        if not date_str:
            return JsonResponse({'error': 'date required'}, status=400)
        try:
            day = _date.fromisoformat(date_str)
        except ValueError:
            return JsonResponse({'error': 'invalid date'}, status=400)
        # Upsert: one link per owner+date
        link, _ = DayShareLink.objects.update_or_create(
            owner=user,
            date=day,
            is_permanent=False,
            defaults={
                'busy_slots': busy_slots,
                'slot_duration_minutes': slot_min,
                'day_start': day_start,
                'day_end': day_end,
            },
        )

    book_url = request.build_absolute_uri(
        reverse('recordings:booking_page', kwargs={'token': link.share_token})
    )
    return JsonResponse({'ok': True, 'url': book_url, 'token': str(link.share_token)})


def booking_page(request, token):
    """Публичная страница свободных слотов (без авторизации)."""
    from datetime import datetime, timedelta, date as _date
    link = get_object_or_404(DayShareLink, share_token=token)

    # Для постоянной ссылки — всегда завтра
    display_date = (_date.today() + timedelta(days=1)) if link.is_permanent else link.date

    def hm(s):
        h, m = map(int, s.split(':'))
        return h * 60 + m

    def fmt(total_min):
        return f'{total_min // 60:02d}:{total_min % 60:02d}'

    start_min = hm(link.day_start)
    end_min = hm(link.day_end)
    dur = link.slot_duration_minutes

    # Строим список занятых интервалов
    busy = []
    busy_named = []  # [(start_min, end_min, title)]
    for b in (link.busy_slots or []):
        bs = hm(b['start'])
        be = hm(b['end'])
        busy.append((bs, be))
        busy_named.append((bs, be, b.get('title', '')))

    # Добавляем повторяющиеся занятые события владельца
    from .models import RecurringBusyTime as _RBT
    import calendar as _cal
    _weekday = display_date.weekday() if display_date else None  # 0=пн, 6=вс
    _is_weekday = _weekday is not None and _weekday < 5
    for rbt in _RBT.objects.filter(owner=link.owner, is_active=True):
        applies = (rbt.repeat == 'daily') or (rbt.repeat == 'weekdays' and _is_weekday)
        if applies:
            bs = rbt.start_time.hour * 60 + rbt.start_time.minute
            be = rbt.end_time.hour * 60 + rbt.end_time.minute
            busy.append((bs, be))
            busy_named.append((bs, be, rbt.title))
    busy.sort()

    # Генерируем все слоты и исключаем занятые
    free_slots = []
    cur = start_min
    while cur + dur <= end_min:
        slot_end = cur + dur
        # Проверяем пересечение с занятыми
        overlap = any(bs < slot_end and be > cur for bs, be in busy)
        if not overlap:
            free_slots.append({'start': fmt(cur), 'end': fmt(slot_end)})
        cur += dur

    # Формируем список занятых для отображения (включая повторяющиеся)
    busy_display = [{'start': fmt(bs), 'end': fmt(be), 'title': title}
                    for bs, be, title in sorted(busy_named) if title]
    return render(request, 'recordings/booking.html', {
        'link': link,
        'display_date': display_date,
        'free_slots': free_slots,
        'busy_slots': link.busy_slots,
        'busy_display': busy_display,
    })


@csrf_exempt
@require_http_methods(['POST'])
def book_slot(request, token):
    """Гость бронирует слот: создаёт LiveKit комнату и отправляет TG-уведомление владельцу."""
    import json as _json
    import uuid
    import hashlib
    from datetime import timedelta as _td
    from . import telegram_service

    from datetime import timedelta, date as _date
    link = get_object_or_404(DayShareLink, share_token=token)
    actual_date = (_date.today() + timedelta(days=1)) if link.is_permanent else link.date
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    slot_start = data.get('slot_start', '')
    slot_end = data.get('slot_end', '')
    guest_name = data.get('guest_name', '').strip() or 'Гость'
    guest_phone = data.get('guest_phone', '').strip()
    agenda = data.get('agenda', '').strip()
    with_mascot = bool(data.get('with_mascot', False))

    if not slot_start or not slot_end:
        return JsonResponse({'error': 'slot_start and slot_end required'}, status=400)
    if not guest_phone:
        return JsonResponse({'error': 'Укажите номер телефона'}, status=400)
    if not agenda:
        return JsonResponse({'error': 'Укажите повестку встречи'}, status=400)

    # Rate limiting: max 1 booking per hour per IP+fingerprint
    from .models import BookingAttempt
    from django.db.models import Q as _Q
    ip = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', '127.0.0.1')
    )
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    resolution = data.get('resolution', '')
    fingerprint = hashlib.md5(f'{user_agent}|{resolution}'.encode()).hexdigest()

    three_hours_ago = timezone.now() - _td(hours=3)
    recent = BookingAttempt.objects.filter(
        created_at__gte=three_hours_ago,
    ).filter(
        _Q(ip=ip) | _Q(fingerprint=fingerprint)
    ).exists()
    is_en = data.get('lang', 'ru') == 'en'
    if recent:
        msg = (
            'You have already booked a meeting recently. Please try again in 3 hours.'
            if is_en else
            'Вы уже бронировали встречу недавно. Попробуйте снова через 3 часа.'
        )
        return JsonResponse({'error': msg, 'rate_limited': True}, status=429)

    # Log attempt
    BookingAttempt.objects.create(ip=ip, fingerprint=fingerprint)

    # Создаём LiveKit комнату
    date_slug = actual_date.strftime('%Y%m%d')
    time_slug = slot_start.replace(':', '')
    base_name = f'booking-{date_slug}-{time_slug}'
    room_name = base_name
    suffix = 1
    while MeetingRoom.objects.filter(room_name=room_name).exists():
        room_name = f'{base_name}-{suffix}'
        suffix += 1

    title = f'{guest_name} / {actual_date.strftime("%d.%m.%Y")} {slot_start}–{slot_end}'
    # Вычисляем scheduled_at из даты + времени слота
    from datetime import datetime as _dt2
    try:
        h, m = map(int, slot_start.split(':'))
        scheduled_at = timezone.make_aware(_dt2(actual_date.year, actual_date.month, actual_date.day, h, m))
    except Exception:
        scheduled_at = None

    meeting = MeetingRoom.objects.create(
        room_name=room_name,
        title=title,
        with_mascot=with_mascot,
        created_by=link.owner,
        space=link.owner.space if link.owner else None,
        scheduled_at=scheduled_at,
    )

    # Владелец автоматически участник встречи
    MeetingAttendee.objects.get_or_create(
        user=link.owner, meeting=meeting,
        defaults={'notify_before_minutes': 15},
    )

    # Помечаем слот как занятый — чтобы следующий бронирующий его не видел
    booked_busy = list(link.busy_slots or [])
    booked_busy.append({'start': slot_start, 'end': slot_end, 'title': guest_name})
    link.busy_slots = booked_busy
    link.save(update_fields=['busy_slots'])

    join_url = f'https://meet.business-pad.com/rooms/{room_name}'

    # TG-уведомление владельцу
    if link.owner.tg_chat_id:
        msg = (
            f'📅 *Новое бронирование!*\n\n'
            f'👤 {guest_name}'
            + (f'\n📞 {guest_phone}' if guest_phone else '')
            + (f'\n📝 {agenda}' if agenda else '')
            + f'\n📆 {actual_date.strftime("%d.%m.%Y")}, {slot_start}–{slot_end}\n\n'
            f'🔗 {join_url}'
        )
        try:
            telegram_service.send_message(link.owner.tg_chat_id, msg)
        except Exception:
            pass

    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''
    if not bot_username:
        from .telegram_service import get_bot_info
        bot_username = get_bot_info().get('username', '')

    return JsonResponse({
        'ok': True,
        'room_name': room_name,
        'join_url': join_url,
        'bot_username': bot_username,
        'slot_start': slot_start,
        'slot_end': slot_end,
        'date': actual_date.strftime('%d.%m.%Y'),
        'title': title,
    })


@csrf_exempt
def api_room_config(request, room_name):
    """Конфиг комнаты для монитора маскота. Auth: X-Agent-Key."""
    key = request.headers.get('X-Agent-Key', '')
    if key != getattr(settings, 'MASTER_API_KEY', ''):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        meeting = MeetingRoom.objects.get(room_name=room_name)
        return JsonResponse({'with_mascot': meeting.with_mascot})
    except MeetingRoom.DoesNotExist:
        return JsonResponse({'with_mascot': False})


@csrf_exempt
@require_http_methods(['GET'])
def api_active_meetings(request):
    """Активные встречи для бота. Auth: X-Agent-Key. Params: chat_id или space_slug."""
    key = request.headers.get('X-Agent-Key', '')
    if key != getattr(settings, 'MASTER_API_KEY', ''):
        return JsonResponse({'error': 'forbidden'}, status=403)

    chat_id = request.GET.get('chat_id')
    space_slug = request.GET.get('space_slug')

    space = None
    if chat_id:
        user = SiteUser.objects.filter(tg_chat_id=int(chat_id)).first()
        space = user.space if user else None
    elif space_slug:
        from recordings.models import Space
        space = Space.objects.filter(slug=space_slug).first()
    else:
        return JsonResponse({'error': 'chat_id or space_slug required'}, status=400)

    if not space:
        return JsonResponse({'meetings': []})

    active_room_names = set()
    participant_counts = {}

    lk_url = getattr(settings, 'LIVEKIT_URL', '').replace('ws://', 'http://').replace('wss://', 'https://')
    lk_key = getattr(settings, 'LIVEKIT_API_KEY', '')
    lk_secret = getattr(settings, 'LIVEKIT_API_SECRET', '')

    if lk_url and lk_key and lk_secret:
        try:
            import asyncio
            from livekit import api as lkapi

            async def _fetch():
                async with lkapi.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
                    resp = await lk.room.list_rooms(lkapi.ListRoomsRequest())
                    for r in resp.rooms:
                        if r.num_participants > 0:
                            active_room_names.add(r.name)
                            participant_counts[r.name] = r.num_participants

            asyncio.run(_fetch())
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning('LiveKit rooms fetch failed: %s', e)

    from django.utils import timezone as _tz
    import datetime as _dt
    _now = _tz.now()
    # Автоматически завершаем встречи без ended_at если прошло > 30 мин с scheduled_at
    MeetingRoom.objects.filter(
        space=space,
        ended_at__isnull=True,
        scheduled_at__lt=_now - _dt.timedelta(minutes=30),
    ).update(ended_at=models.F('scheduled_at') + _dt.timedelta(minutes=30))

    db_rooms = MeetingRoom.objects.filter(space=space, ended_at__isnull=True).order_by('-created_at')
    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')

    if active_room_names:
        rooms = [r for r in db_rooms if r.room_name in active_room_names]
    else:
        rooms = list(db_rooms[:8])

    return JsonResponse({'meetings': [
        {
            'room_name': rm.room_name,
            'title': rm.title,
            'participants': participant_counts.get(rm.room_name, 0),
            'url': f'{site_url}/meetings/{rm.room_name}/',
        }
        for rm in rooms
    ]})


# ── Bot Chat History ──────────────────────────────────────────────────────────

@site_login_required
def bot_history(request):
    """История переписок: кастомные боты видны только своему владельцу."""
    user = get_current_user(request)
    space = user.space

    # Пользователи пространства с их chat_id (для отображения имён собеседников)
    space_users = SiteUser.objects.filter(space=space, tg_chat_id__isnull=False).values('tg_chat_id', 'email')
    space_chat_ids = {u['tg_chat_id']: u['email'] for u in space_users}

    # Только МОИ кастомные боты — переписки чужих ботов недоступны
    my_bots = {b.pk: b for b in CustomBot.objects.filter(owner=user, is_active=True)}
    own_bot_ids = list(my_bots.keys())
    own_chat_id = user.tg_chat_id

    # Переписки: мои боты (все их чаты) + моя переписка с главным ботом
    from django.db.models import Q as _Q
    history_filter = _Q(bot_id__in=own_bot_ids)
    if own_chat_id:
        history_filter |= _Q(bot_id__isnull=True, chat_id=own_chat_id)

    convs_qs = (
        BotChatHistory.objects
        .filter(history_filter)
        .values('chat_id', 'bot_id')
        .distinct()
        .order_by('bot_id', 'chat_id')
    )

    selected_chat_id = request.GET.get('chat_id')
    selected_bot_id = request.GET.get('bot_id', '')
    try:
        selected_chat_id = int(selected_chat_id) if selected_chat_id else None
        selected_bot_id_int = int(selected_bot_id) if selected_bot_id else None
    except (ValueError, TypeError):
        selected_chat_id = None
        selected_bot_id_int = None

    # Список разговоров для сайдбара
    conversations = []
    for c in convs_qs:
        cid = c['chat_id']
        bid = c['bot_id']
        last_msg = BotChatHistory.objects.filter(chat_id=cid, bot_id=bid).order_by('-created_at').first()
        bot_name = my_bots[bid].username if bid and bid in my_bots else 'Главный бот'
        conversations.append({
            'chat_id': cid,
            'bot_id': bid,
            'bot_id_str': str(bid) if bid is not None else '',
            'email': space_chat_ids.get(cid, f'TG {cid}'),
            'bot_name': bot_name,
            'last_at': last_msg.created_at if last_msg else None,
            'active': cid == selected_chat_id and bid == selected_bot_id_int,
        })

    # Сообщения выбранного разговора — проверяем что bot принадлежит мне
    messages_list = []
    if selected_chat_id is not None:
        # Безопасность: запрещаем смотреть переписки чужих ботов напрямую по URL
        can_view = (
            (selected_bot_id_int is None and own_chat_id == selected_chat_id) or
            (selected_bot_id_int is not None and selected_bot_id_int in my_bots)
        )
        if can_view:
            qs = (
                BotChatHistory.objects
                .filter(chat_id=selected_chat_id, bot_id=selected_bot_id_int)
                .select_related('recording')
                .order_by('created_at')
            )
            messages_list = list(qs)

    return render(request, 'recordings/bot_history.html', {
        'conversations': conversations,
        'messages_list': messages_list,
        'selected_chat_id': selected_chat_id,
        'selected_bot_id': selected_bot_id,
        'space_chat_ids': space_chat_ids,
        'space_bots': my_bots,
    })


@site_login_required
@require_http_methods(['POST'])
def bot_history_insert_transcription(request, history_id):
    """Подставить транскрибацию вместо/после аудио-сообщения в истории."""
    entry = get_object_or_404(BotChatHistory, pk=history_id)
    rec = entry.recording

    if not rec:
        return JsonResponse({'error': 'Нет связанной записи.'}, status=400)
    if rec.status != Recording.Status.DONE:
        return JsonResponse({'error': f'Транскрибация ещё не готова (статус: {rec.status}).'}, status=400)
    if not rec.transcription:
        return JsonResponse({'error': 'Транскрибация пуста.'}, status=400)

    # Обновляем контент записи истории — вставляем транскрибацию
    transcription_preview = rec.transcription[:3000]
    if len(rec.transcription) > 3000:
        transcription_preview += '\n...[обрезано]'

    entry.content = f'🎙 [{rec.filename}]\n\n{transcription_preview}'
    entry.save(update_fields=['content'])

    # Также добавляем транскрибацию в активный контекст агента (как user-сообщение)
    # чтобы можно было задавать вопросы по этому аудио
    BotChatHistory.add(
        entry.chat_id, entry.bot_id, 'user',
        f'[Транскрибация аудио «{rec.filename}»]\n{rec.transcription[:2000]}',
    )

    return JsonResponse({
        'ok': True,
        'content': entry.content,
        'filename': rec.filename,
        'rec_id': rec.pk,
    })


# ── Bot Chat Sessions (admin) ─────────────────────────────────────────────────

from datetime import timedelta as _timedelta

SESSION_GAP_HOURS = 1


def _build_sessions(space):
    """
    Разбивает историю переписок на сессии (промежуток между сообщениями >= SESSION_GAP_HOURS).
    Возвращает список dict: {chat_id, bot_id, messages, start_at, end_at, ...}.
    """
    # Пользователи пространства
    space_users = {
        u['tg_chat_id']: u['email']
        for u in SiteUser.objects.filter(space=space, tg_chat_id__isnull=False).values('tg_chat_id', 'email')
    }
    # Кастомные боты пространства
    space_bots = {b.pk: b for b in CustomBot.objects.filter(space=space, is_active=True)}

    # Все сообщения всех пользователей пространства, отсортированные
    all_msgs = list(
        BotChatHistory.objects
        .filter(chat_id__in=list(space_users.keys()))
        .select_related('recording')
        .order_by('chat_id', 'bot_id', 'created_at')
    )

    gap = _timedelta(hours=SESSION_GAP_HOURS)
    sessions = []
    cur = None

    for msg in all_msgs:
        key = (msg.chat_id, msg.bot_id)
        if cur is None or cur['key'] != key or (msg.created_at - cur['end_at']) >= gap:
            if cur:
                sessions.append(cur)
            # first user message as preview
            preview = msg.content[:80] if msg.role == 'user' else ''
            cur = {
                'key': key,
                'chat_id': msg.chat_id,
                'bot_id': msg.bot_id,
                'messages': [msg],
                'start_at': msg.created_at,
                'end_at': msg.created_at,
                'preview': preview,
            }
        else:
            cur['messages'].append(msg)
            cur['end_at'] = msg.created_at
            if not cur['preview'] and msg.role == 'user':
                cur['preview'] = msg.content[:80]

    if cur:
        sessions.append(cur)

    # Аннотируем каждую сессию
    now = timezone.now()
    result = []
    for s in sessions:
        user_msgs = [m for m in s['messages'] if m.role in ('user', 'audio')]
        bot_name = space_bots[s['bot_id']].username if s['bot_id'] and s['bot_id'] in space_bots else 'Главный бот'
        s['email'] = space_users.get(s['chat_id'], f'TG {s["chat_id"]}')
        s['bot_name'] = bot_name
        s['user_msg_count'] = len(user_msgs)
        s['total_count'] = len(s['messages'])
        s['has_audio'] = any(m.role == 'audio' for m in s['messages'])
        s['completed'] = (now - s['end_at']) >= gap
        s['session_id'] = f"{s['chat_id']}_{s['bot_id'] or 0}_{int(s['start_at'].timestamp())}"
        result.append(s)

    # Сортируем: сначала завершённые, внутри — новые сверху
    result.sort(key=lambda x: x['end_at'], reverse=True)
    return result, space_users, space_bots


@site_login_required
def bot_sessions(request):
    """Страница сессий переписок с ботами. Завершённая сессия = час без сообщений."""
    user = get_current_user(request)
    space = user.space
    if not space:
        return render(request, 'recordings/bot_sessions.html', {'sessions': [], 'selected': None})

    sessions, space_users, space_bots = _build_sessions(space)

    # Только завершённые по умолчанию, если не запрошены все
    show_active = request.GET.get('show_active') == '1'
    if not show_active:
        display_sessions = [s for s in sessions if s['completed']]
    else:
        display_sessions = sessions

    selected_id = request.GET.get('s')
    selected = None
    if selected_id:
        selected = next((s for s in sessions if s['session_id'] == selected_id), None)

    return render(request, 'recordings/bot_sessions.html', {
        'sessions': display_sessions,
        'selected': selected,
        'show_active': show_active,
        'total_completed': sum(1 for s in sessions if s['completed']),
        'total_active': sum(1 for s in sessions if not s['completed']),
    })


@site_login_required
@require_http_methods(['POST'])
def bot_history_insert_ocr(request, history_id):
    """Подставить результат OCR вместо ocr-сообщения в истории."""
    entry = get_object_or_404(BotChatHistory, pk=history_id)
    job = entry.ocr_job
    if not job:
        return JsonResponse({'error': 'Нет связанной OCR-задачи.'}, status=400)
    if job.status != 'done' or not job.result_markdown:
        return JsonResponse({'error': f'OCR ещё не готов (статус: {job.status}).'}, status=400)

    preview = job.result_markdown[:3000]
    if len(job.result_markdown) > 3000:
        preview += '\n...[обрезано]'

    entry.content = f'📷 [{job.original_filename}]\n\n{preview}'
    entry.save(update_fields=['content'])

    # Добавляем в контекст агента
    BotChatHistory.add(
        entry.chat_id, entry.bot_id, 'user',
        f'[Результат OCR «{job.original_filename}»]\n{job.result_markdown[:2000]}',
    )
    return JsonResponse({'ok': True, 'content': entry.content, 'filename': job.original_filename})


@site_login_required
@require_http_methods(['POST'])
def bot_history_delete_entry(request, history_id):
    """Удалить одну запись из истории чата."""
    entry = get_object_or_404(BotChatHistory, pk=history_id)
    entry.delete()
    return JsonResponse({'ok': True})


# ─── Яндекс Вики → импорт в базу знаний ────────────────────────────────────

_YA_WIKI_BASE = 'https://api.wiki.yandex.net/v1'


def _ya_headers(token, org_id):
    return {
        'Authorization': f'OAuth {token}',
        'X-Org-Id': str(org_id),
    }


@staff_member_required
def yadoc_import(request):
    return render(request, 'admin/recordings/yadoc_import.html')


@staff_member_required
@require_http_methods(['POST'])
def yadoc_fetch_tree(request):
    """Получить 2-уровневое дерево страниц из Яндекс Вики (AJAX)."""
    import requests as rq

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Неверный JSON'}, status=400)

    token = body.get('token', '').strip()
    org_id = body.get('org_id', '').strip()
    if not token or not org_id:
        return JsonResponse({'error': 'Укажите токен и Org-ID'}, status=400)

    headers = _ya_headers(token, org_id)
    all_pages = []
    next_page_token = None

    try:
        while True:
            params = {'fields': 'id,slug,title,parentId', 'limit': 100}
            if next_page_token:
                params['pageToken'] = next_page_token
            resp = rq.get(f'{_YA_WIKI_BASE}/pages', headers=headers, params=params, timeout=20)
            if resp.status_code == 401:
                return JsonResponse({'error': 'Неверный токен или Org-ID (401)'}, status=401)
            if resp.status_code == 403:
                return JsonResponse({'error': 'Нет доступа. Проверьте права токена (403)'}, status=403)
            if resp.status_code != 200:
                return JsonResponse({'error': f'Яндекс вернул {resp.status_code}: {resp.text[:300]}'}, status=400)
            data = resp.json()
            pages = data.get('pages', data.get('items', []))
            all_pages.extend(pages)
            next_page_token = data.get('nextPageToken')
            if not next_page_token or len(all_pages) >= 1000:
                break
    except rq.RequestException as e:
        return JsonResponse({'error': f'Ошибка сети: {e}'}, status=500)

    # Строим дерево 2 уровней
    page_map = {p['id']: p for p in all_pages}
    children_map: dict = {}
    roots = []

    for p in all_pages:
        pid = p.get('parentId')
        if pid and pid in page_map:
            children_map.setdefault(pid, []).append(p)
        else:
            roots.append(p)

    def node(p, depth=0):
        kids = []
        if depth < 1:
            kids = [node(c, depth + 1) for c in sorted(
                children_map.get(p['id'], []), key=lambda x: x.get('slug', ''))]
        return {
            'id': p['id'],
            'title': p.get('title') or p.get('slug', p['id']),
            'slug': p.get('slug', ''),
            'children': kids,
        }

    tree = [node(r) for r in sorted(roots, key=lambda x: x.get('slug', ''))]
    return JsonResponse({'tree': tree, 'total': len(all_pages)})


@staff_member_required
@require_http_methods(['POST'])
def yadoc_start_import(request):
    """Сохранить параметры задания в сессию."""
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Неверный JSON'}, status=400)

    request.session['yadoc_job'] = {
        'token':        body.get('token', ''),
        'org_id':       body.get('org_id', ''),
        'selected_ids': body.get('selected_ids', []),
        'tree':         body.get('tree', []),
    }
    return JsonResponse({'ok': True})


@staff_member_required
def yadoc_stream(request):
    """SSE-поток: скачивает выбранные страницы и создаёт статьи в wiki_kb."""
    import requests as rq
    from wiki_kb.models import WikiArticle

    job = request.session.get('yadoc_job')
    if not job:
        def _err():
            yield 'data: {"type":"error","msg":"Задание не найдено — начните заново"}\n\n'
        return StreamingHttpResponse(_err(), content_type='text/event-stream')

    token = job['token']
    org_id = job['org_id']
    selected_ids = set(job['selected_ids'])
    full_tree = job['tree']
    headers = _ya_headers(token, org_id)

    def generate():
        def ev(data):
            return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'

        def unique_slug(base):
            parts = [slugify(p) for p in base.strip('/').split('/') if p]
            candidate = ('-'.join(parts) or 'page')[:200]
            orig, i = candidate, 0
            while WikiArticle.objects.filter(slug=candidate).exists():
                i += 1
                candidate = f'{orig}-{i}'
            return candidate

        def fetch_body(page_id):
            try:
                r = rq.get(f'{_YA_WIKI_BASE}/pages/{page_id}',
                           headers=headers, params={'fields': 'body'}, timeout=20)
                if r.status_code == 200:
                    d = r.json()
                    return d.get('body') or d.get('text') or ''
            except Exception:
                pass
            return ''

        # Корневая статья
        today = date.today().strftime('%Y-%m-%d')
        root_title = f'Яндекс {today}'
        root_slug = unique_slug(f'yandex-{today}')
        root_article = WikiArticle.objects.create(
            title=root_title, slug=root_slug,
            content='Страницы, импортированные из Яндекс Вики.',
        )
        yield ev({'type': 'log', 'msg': f'📁 Создана корневая статья «{root_title}»'})

        selected_nodes = [n for n in full_tree if n['id'] in selected_ids]
        total = sum(1 + len(n.get('children', [])) for n in selected_nodes)
        yield ev({'type': 'total', 'total': total})
        done = 0

        for node in selected_nodes:
            body = fetch_body(node['id'])
            slug1 = unique_slug(node.get('slug') or node['id'])
            parent_art = WikiArticle.objects.create(
                title=node.get('title') or 'Без названия',
                slug=slug1, content=body, parent=root_article,
            )
            done += 1
            yield ev({'type': 'progress', 'done': done, 'total': total,
                      'msg': f'📄 {node.get("title") or node["id"]}'})

            for child in node.get('children', []):
                c_body = fetch_body(child['id'])
                c_slug = unique_slug(child.get('slug') or child['id'])
                WikiArticle.objects.create(
                    title=child.get('title') or 'Без названия',
                    slug=c_slug, content=c_body, parent=parent_art,
                )
                done += 1
                yield ev({'type': 'progress', 'done': done, 'total': total,
                          'msg': f'  └─ {child.get("title") or child["id"]}'})
                time.sleep(0.05)

        yield ev({'type': 'done',
                  'msg': f'✅ Готово! Добавлено {done} страниц.',
                  'wiki_url': f'/kb/{root_slug}/'})

    response = StreamingHttpResponse(generate(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@site_login_required
def google_calendar_auth(request):
    """Redirect user to Google OAuth for Calendar access."""
    from django.conf import settings as _s
    import urllib.parse
    client_id = getattr(_s, 'GOOGLE_CLIENT_ID', '')
    if not client_id:
        return JsonResponse({'error': 'Google OAuth не настроен (GOOGLE_CLIENT_ID)'}, status=503)
    redirect_uri = request.build_absolute_uri('/meetings/google-callback/')
    params = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/calendar.readonly',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': 'gcal',
    })
    return redirect(f'https://accounts.google.com/o/oauth2/v2/auth?{params}')


def google_calendar_callback(request):
    """Handle Google OAuth callback, save tokens to SiteUser."""
    from django.conf import settings as _s
    import urllib.request as _ur
    import urllib.parse
    user = get_current_user(request)
    if not user:
        return redirect('/login/')
    code = request.GET.get('code', '')
    if not code:
        return redirect('/meetings/?gcal_error=no_code')
    client_id = getattr(_s, 'GOOGLE_CLIENT_ID', '')
    client_secret = getattr(_s, 'GOOGLE_CLIENT_SECRET', '')
    redirect_uri = request.build_absolute_uri('/meetings/google-callback/')
    payload = urllib.parse.urlencode({
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode()
    req = _ur.Request('https://oauth2.googleapis.com/token', data=payload,
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with _ur.urlopen(req, timeout=10) as r:
            token_data = json.loads(r.read())
    except Exception as e:
        logger.error('Google OAuth token exchange failed: %s', e)
        return redirect('/meetings/?gcal_error=token_failed')
    user.google_calendar_token = token_data
    user.save(update_fields=['google_calendar_token'])
    return redirect('/meetings/?gcal_ok=1')


@site_login_required
@require_http_methods(['POST'])
def google_calendar_import(request):
    """Import upcoming events from Google Calendar into MeetingRoom."""
    import urllib.request as _ur
    import urllib.parse
    from django.conf import settings as _s
    from django.utils import timezone
    from datetime import timedelta, datetime as _dt

    user = get_current_user(request)
    token_data = user.google_calendar_token if user else None
    if not token_data or not token_data.get('access_token'):
        return JsonResponse({'error': 'not_connected'}, status=400)

    def _refresh_token(td):
        client_id = getattr(_s, 'GOOGLE_CLIENT_ID', '')
        client_secret = getattr(_s, 'GOOGLE_CLIENT_SECRET', '')
        if not td.get('refresh_token'):
            return td
        payload = urllib.parse.urlencode({
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': td['refresh_token'],
            'grant_type': 'refresh_token',
        }).encode()
        req = _ur.Request('https://oauth2.googleapis.com/token', data=payload,
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with _ur.urlopen(req, timeout=10) as r:
                new_td = json.loads(r.read())
            td['access_token'] = new_td['access_token']
            if 'refresh_token' in new_td:
                td['refresh_token'] = new_td['refresh_token']
            return td
        except Exception as e:
            logger.warning('Google token refresh failed: %s', e)
            return td

    def _fetch_events(access_token):
        now_str = timezone.now().isoformat().replace('+00:00', 'Z')
        future_str = (timezone.now() + timedelta(days=60)).isoformat().replace('+00:00', 'Z')
        params = urllib.parse.urlencode({
            'timeMin': now_str,
            'timeMax': future_str,
            'singleEvents': 'true',
            'orderBy': 'startTime',
            'maxResults': '100',
        })
        req = _ur.Request(
            f'https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        with _ur.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    # Try to fetch; if 401, refresh and retry once
    access_token = token_data['access_token']
    try:
        data = _fetch_events(access_token)
    except Exception as e:
        if '401' in str(e):
            token_data = _refresh_token(token_data)
            user.google_calendar_token = token_data
            user.save(update_fields=['google_calendar_token'])
            access_token = token_data.get('access_token', '')
            try:
                data = _fetch_events(access_token)
            except Exception as e2:
                return JsonResponse({'error': str(e2)}, status=502)
        else:
            return JsonResponse({'error': str(e)}, status=502)

    items = data.get('items', [])
    created = 0
    skipped = 0
    import uuid as _uuid_mod

    def _parse_gcal_dt(s):
        if not s:
            return None
        for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
            try:
                dt = _dt.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = timezone.make_aware(dt)
                return dt
            except ValueError:
                pass
        return None

    for item in items:
        if item.get('status') == 'cancelled':
            skipped += 1
            continue
        google_id = item.get('id', '')
        title = item.get('summary', '').strip() or 'Без названия'
        start_raw = item.get('start', {}).get('dateTime') or item.get('start', {}).get('date', '')
        end_raw = item.get('end', {}).get('dateTime') or item.get('end', {}).get('date', '')
        scheduled_at = _parse_gcal_dt(start_raw)
        ended_at = _parse_gcal_dt(end_raw)
        join_url = (item.get('hangoutLink') or
                    item.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', '') or '')

        # Skip if already imported
        if google_id and MeetingRoom.objects.filter(google_event_id=google_id).exists():
            skipped += 1
            continue

        room_name = _uuid_mod.uuid4().hex[:12]
        while MeetingRoom.objects.filter(room_name=room_name).exists():
            room_name = _uuid_mod.uuid4().hex[:12]

        MeetingRoom.objects.create(
            room_name=room_name,
            title=title,
            with_mascot=False,
            created_by=user,
            space=user.space if user else None,
            scheduled_at=scheduled_at,
            ended_at=ended_at,
            join_url=join_url or None,
            google_event_id=google_id or None,
        )
        created += 1

    return JsonResponse({'ok': True, 'created': created, 'skipped': skipped})


# ── Gonka AI панель ────────────────────────────────────────────────────────────

_GONKA_NODES = [
    'https://node3.gonka.ai',
    'http://node1.gonka.ai:8000',
    'http://node2.gonka.ai:8000',
]
_GONKA_EVM_ADDR = '0xB807a354cd0CBb955B5c90fC5F955428c82E6c45'
_GONKA_ADDR = 'gonka12cf6d7346f2k6fe4hsl9nnehhzxnh0daw74nws'


def _get_gonka_private_key():
    import os
    return os.environ.get('GONKA_PRIVATE_KEY', '')


@site_login_required
def gonka_panel(request):
    """Секретная страница управления Gonka AI."""
    import json as _json
    import os as _os

    saved = False
    error = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'set_node':
            node_url = request.POST.get('node_url', '').strip()
            if node_url:
                SystemConfig.set('gonka_node_url', node_url)
                saved = True

    pk = _get_gonka_private_key()
    pk_masked = pk[:6] if pk else '??????'

    current_node = SystemConfig.get('gonka_node_url', _GONKA_NODES[0])
    model = SystemConfig.get('gonka_model', 'Qwen/Qwen3-235B-A22B-Instruct-2507-FP8')

    return render(request, 'recordings/gonka_panel.html', {
        'evm_address': _GONKA_EVM_ADDR,
        'gonka_address': _GONKA_ADDR,
        'pk_masked': pk_masked,
        'pk_full': pk,
        'nodes': _GONKA_NODES,
        'nodes_json': _json.dumps(_GONKA_NODES),
        'current_node': current_node,
        'model': model,
        'saved': saved,
        'error': error,
    })


@csrf_exempt
@require_http_methods(['POST'])
def gonka_api_node_check(request):
    """Проверить доступность Gonka-нода."""
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad json'}, status=400)

    node = data.get('node', '').strip()
    if not node:
        return JsonResponse({'ok': False, 'error': 'node required'}, status=400)

    try:
        import httpx as _httpx
        r = _httpx.get(f'{node}/v1/identity', timeout=5)
        if r.status_code == 200:
            d = r.json()
            block = d.get('data', {}).get('block', '?')
            return JsonResponse({'ok': True, 'block': block})
        return JsonResponse({'ok': False, 'error': f'HTTP {r.status_code}'})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)[:100]})


@csrf_exempt
@require_http_methods(['POST'])
def gonka_api_test(request):
    """Тестовый инференс-запрос через Gonka."""
    import json as _json
    import os as _os
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    prompt = data.get('prompt', '').strip()
    if not prompt:
        return JsonResponse({'error': 'prompt required'}, status=400)

    pk = _get_gonka_private_key()
    if not pk:
        return JsonResponse({'error': 'GONKA_PRIVATE_KEY не задан'})

    node = SystemConfig.get('gonka_node_url', 'https://node3.gonka.ai')
    model = SystemConfig.get('gonka_model', 'Qwen/Qwen3-235B-A22B-Instruct-2507-FP8')

    try:
        from gonka_openai import GonkaOpenAI
        client = GonkaOpenAI(gonka_private_key=pk, source_url=node)
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        return JsonResponse({
            'result': resp.choices[0].message.content,
            'model': model,
            'node': node,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)[:400]})


@require_http_methods(['GET'])
def db_export_download(request, filename):
    """Скачать Excel-файл, сгенерированный DB-агентом."""
    import re
    import mimetypes
    from django.http import FileResponse, Http404

    # Только безопасные имена: chemico_export_YYYYMMDD_HHMMSS.xlsx
    if not re.fullmatch(r'chemico_export_\d{8}_\d{6}\.xlsx', filename):
        raise Http404

    export_dir = settings.MEDIA_ROOT / 'db_exports'
    fpath = export_dir / filename
    if not fpath.exists():
        raise Http404

    mime, _ = mimetypes.guess_type(str(fpath))
    response = FileResponse(open(fpath, 'rb'), content_type=mime or 'application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Web Chat API ─────────────────────────────────────────────────────────────

def _get_chat_session_id(request) -> int:
    """Stable integer chat_id from session key."""
    if not request.session.session_key:
        request.session.create()
    key = request.session.session_key
    return abs(hash(key)) % (2**31)


@csrf_exempt
@require_http_methods(['GET'])
def chat_page(request, token):
    """Public web chat page for a CustomBot."""
    from recordings.models import CustomBot
    try:
        import uuid as _uuid
        bot = CustomBot.objects.get(public_chat_token=_uuid.UUID(str(token)), is_active=True)
    except (CustomBot.DoesNotExist, ValueError):
        from django.http import Http404
        raise Http404
    return render(request, 'recordings/chat.html', {'bot': bot})


@csrf_exempt
@require_http_methods(['POST'])
def chat_send(request, token):
    """REST: send text message to the agent."""
    import json as _json
    from recordings.models import CustomBot, BotChatHistory
    try:
        import uuid as _uuid
        bot = CustomBot.objects.get(public_chat_token=_uuid.UUID(str(token)), is_active=True)
    except (CustomBot.DoesNotExist, ValueError):
        return JsonResponse({'error': 'not found'}, status=404)

    try:
        data = _json.loads(request.body)
        question = data.get('message', '').strip()
    except Exception:
        return JsonResponse({'error': 'bad request'}, status=400)

    if not question:
        return JsonResponse({'error': 'empty message'}, status=400)

    chat_id = _get_chat_session_id(request)

    # Route to db_agent or wiki_rag based on bot config
    from recordings.models import SystemConfig
    answer_mode = SystemConfig.get(f'db_bot_{bot.pk}_answer_mode', 'wiki_rag')

    if answer_mode == 'db_agent':
        from chemico_agent.agent import ask
        result = ask(question, chat_id=chat_id, bot_id=bot.pk)
        return JsonResponse({
            'text': result['text'],
            'excel_url': result.get('excel_url'),
            'provider': result.get('provider'),
            'model': result.get('model'),
        })
    else:
        # Wiki RAG
        from recordings.bot_agent import run_agent
        space = getattr(bot.owner, 'space', None) if bot.owner else None
        answer, sources = run_agent(
            chat_id=chat_id,
            user_message=question,
            space=space,
            bot_id=bot.pk,
            custom_bot=bot,
            owner=bot.owner,
        )
        return JsonResponse({'text': answer})


@csrf_exempt
@require_http_methods(['POST'])
def chat_upload(request, token):
    """REST: upload file (photo/excel/csv) to the agent."""
    from recordings.models import CustomBot, SystemConfig
    try:
        import uuid as _uuid
        bot = CustomBot.objects.get(public_chat_token=_uuid.UUID(str(token)), is_active=True)
    except (CustomBot.DoesNotExist, ValueError):
        return JsonResponse({'error': 'not found'}, status=404)

    if 'file' not in request.FILES:
        return JsonResponse({'error': 'no file'}, status=400)

    uploaded = request.FILES['file']
    caption = request.POST.get('caption', '').strip()
    chat_id = _get_chat_session_id(request)
    answer_mode = SystemConfig.get(f'db_bot_{bot.pk}_answer_mode', 'wiki_rag')
    fname = uploaded.name.lower()

    import tempfile, os

    # Save to temp file
    suffix = os.path.splitext(uploaded.name)[1] or '.bin'
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        # Excel / CSV handling (only in db_agent mode)
        if fname.endswith(('.xlsx', '.xls', '.csv')) and answer_mode == 'db_agent':
            # Convert CSV to xlsx if needed
            if fname.endswith('.csv'):
                import pandas as pd
                df = pd.read_csv(tmp_path)
                xlsx_path = tmp_path + '.xlsx'
                df.to_excel(xlsx_path, index=False)
                os.unlink(tmp_path)
                tmp_path = xlsx_path

            from chemico_agent.excel_input import ask_about_excel, enrich_excel_from_db, _is_enrich_request
            if _is_enrich_request(caption):
                result = enrich_excel_from_db(tmp_path, caption or 'Дополни файл данными из базы данных')
            else:
                result = ask_about_excel(tmp_path, caption or 'Опиши содержимое файла: сколько строк, какие колонки, ключевые показатели')
            return JsonResponse({
                'text': result['text'],
                'excel_url': result.get('excel_url'),
            })

        # Photo handling — OCR then agent
        if fname.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')):
            # Use OCR server
            import requests as _req
            from django.conf import settings as _s
            ocr_url = getattr(_s, 'OCR_SERVER_URL', 'http://ocr:8001')
            try:
                with open(tmp_path, 'rb') as img_f:
                    ocr_resp = _req.post(f'{ocr_url}/ocr', files={'file': img_f}, timeout=30)
                ocr_text = ocr_resp.json().get('text', '').strip() if ocr_resp.ok else ''
            except Exception:
                ocr_text = ''

            if not ocr_text:
                return JsonResponse({'text': 'Текст на изображении не распознан.'})

            question = f'На изображении распознан текст:\n{ocr_text}\n\n'
            if caption:
                question += f'Вопрос: {caption}'
            else:
                question += 'Что можно сказать по этим данным? Если это финансовые данные — сделай анализ.'

            if answer_mode == 'db_agent':
                from chemico_agent.agent import ask
                result = ask(question, chat_id=chat_id, bot_id=bot.pk)
                return JsonResponse({'text': result['text'], 'ocr_text': ocr_text})
            else:
                from recordings.bot_agent import ask as wiki_ask
                answer = wiki_ask(question, bot=bot, chat_id=chat_id)
                return JsonResponse({'text': answer, 'ocr_text': ocr_text})

        return JsonResponse({'error': 'Неподдерживаемый тип файла'}, status=400)

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@require_http_methods(['GET'])
def chat_history(request, token):
    """REST: get message history for the current session."""
    from recordings.models import CustomBot, BotChatHistory
    try:
        import uuid as _uuid
        bot = CustomBot.objects.get(public_chat_token=_uuid.UUID(str(token)), is_active=True)
    except (CustomBot.DoesNotExist, ValueError):
        return JsonResponse({'error': 'not found'}, status=404)

    chat_id = _get_chat_session_id(request)
    msgs = list(
        BotChatHistory.objects.filter(chat_id=chat_id, bot_id=bot.pk)
        .order_by('created_at')
        .values('role', 'content', 'created_at')[:100]
    )
    for m in msgs:
        m['created_at'] = m['created_at'].isoformat()
    return JsonResponse({'messages': msgs})


# ── Excel Studio ──────────────────────────────────────────────────────────────

@site_login_required
def db_excel_page(request):
    user = get_current_user(request)
    return render(request, 'recordings/db_excel.html', {'user': user})


@site_login_required
@csrf_exempt
def db_excel_upload(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    import pandas as pd, tempfile, os, math
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'Файл не передан'}, status=400)
    suffix = '.xlsx' if f.name.lower().endswith('.xlsx') else '.xls'
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in f.chunks():
            tmp.write(chunk)
        tmp.close()
        engine = 'openpyxl' if suffix == '.xlsx' else 'xlrd'
        df = pd.read_excel(tmp.name, engine=engine)
        columns = [str(c) for c in df.columns]
        rows = []
        for _, row in df.iterrows():
            r = {}
            for k, v in row.items():
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    r[str(k)] = None
                elif hasattr(v, 'isoformat'):
                    r[str(k)] = str(v)
                else:
                    r[str(k)] = v
            rows.append(r)
        return JsonResponse({'columns': columns, 'rows': rows,
                             'filename': f.name, 'total': len(rows)})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@site_login_required
@csrf_exempt
def db_excel_fill_column(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    import pandas as pd, tempfile, os, json as _json, math
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    rows = data.get('rows', [])
    col_name = data.get('col_name', '').strip()
    prompt = data.get('prompt', '').strip()
    key_col = data.get('key_col', '').strip()
    test_mode = data.get('test', False)

    if not rows or not col_name or not prompt:
        return JsonResponse({'error': 'rows, col_name, prompt обязательны'}, status=400)

    work_rows = rows[:3] if test_mode else rows
    df = pd.DataFrame(work_rows)
    orig_cols = set(str(c) for c in df.columns)
    if col_name in df.columns:
        df = df.drop(columns=[col_name])

    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    result_path = None
    try:
        tmp.close()
        df.to_excel(tmp.name, index=False)

        instruction = f"Добавь колонку «{col_name}»: {prompt}."
        if key_col:
            instruction += f" Для сопоставления строк используй поле «{key_col}»."
        instruction += " Сохрани все исходные строки, добавив только запрошенную колонку."

        from chemico_agent.excel_input import enrich_excel_from_db
        result = enrich_excel_from_db(tmp.name, instruction)

        result_path = result.get('excel_path')
        if not result_path or not os.path.exists(str(result_path)):
            return JsonResponse({
                'error': result.get('text', 'Агент не вернул файл'),
                'col_name': col_name,
            }, status=400)

        df_result = pd.read_excel(result_path, engine='openpyxl')

        # Ищем целевую колонку (точное совпадение, потом регистронезависимо)
        target_col = None
        for c in df_result.columns:
            if str(c) == col_name:
                target_col = c
                break
        if not target_col:
            for c in df_result.columns:
                if str(c).lower() == col_name.lower():
                    target_col = c
                    break
        if not target_col:
            new_cols = [c for c in df_result.columns if str(c) not in orig_cols]
            target_col = new_cols[0] if new_cols else None

        if not target_col:
            return JsonResponse({
                'error': 'Агент не создал нужный столбец',
                'col_name': col_name,
                'text': result.get('text', ''),
            }, status=400)

        values = []
        for v in df_result[target_col]:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                values.append(None)
            elif hasattr(v, 'isoformat'):
                values.append(str(v))
            else:
                values.append(v)

        return JsonResponse({
            'col_name': col_name,
            'values': values,
            'total': len(values),
            'text': result.get('text', ''),
        })
    except Exception as e:
        logger.exception('db_excel_fill_column failed')
        return JsonResponse({'error': str(e), 'col_name': col_name}, status=500)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        if result_path:
            try:
                os.unlink(str(result_path))
            except Exception:
                pass


@site_login_required
@csrf_exempt
def db_excel_fill_sse(request):
    """
    SSE streaming fill endpoint.
    Immediately sends a progress event, then blocks while the agent runs,
    then streams the resulting values in chunks for a reactive fill effect.

    Yields SSE events:
      {type: 'progress', message: '...'}
      {type: 'chunk',    start: N, values: [...], total: M}
      {type: 'done',     total: N, text: '...'}
      {type: 'error',    message: '...'}
    """
    if request.method != 'POST':
        from django.http import HttpResponse
        return HttpResponse('POST only', status=405)

    import json as _json, pandas as pd, tempfile, os, math
    try:
        data = _json.loads(request.body)
    except Exception:
        from django.http import HttpResponse
        return HttpResponse('bad json', status=400)

    rows     = data.get('rows', [])
    col_name = data.get('col_name', '').strip()
    prompt   = data.get('prompt', '').strip()
    key_col  = data.get('key_col', '').strip()

    def generate():
        yield 'data: ' + _json.dumps({'type': 'progress', 'message': '⏳ Агент выполняет запрос к базе данных...'}) + '\n\n'

        tmp_path = result_path = None
        try:
            df = pd.DataFrame(rows)
            orig_cols = set(str(c) for c in df.columns)
            if col_name in df.columns:
                df = df.drop(columns=[col_name])

            tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
            tmp.close()
            tmp_path = tmp.name
            df.to_excel(tmp_path, index=False)

            instruction = f'Добавь колонку «{col_name}»: {prompt}.'
            if key_col:
                instruction += f' Для сопоставления строк используй поле «{key_col}».'
            instruction += ' Сохрани все исходные строки, добавив только запрошенную колонку.'

            from chemico_agent.excel_input import enrich_excel_from_db
            result = enrich_excel_from_db(tmp_path, instruction)

            result_path = result.get('excel_path')
            if not result_path or not os.path.exists(str(result_path)):
                yield 'data: ' + _json.dumps({'type': 'error', 'message': result.get('text', 'Агент не вернул файл')}) + '\n\n'
                return

            df_result = pd.read_excel(result_path, engine='openpyxl')

            target_col = next((c for c in df_result.columns if str(c) == col_name), None)
            if not target_col:
                target_col = next((c for c in df_result.columns if str(c).lower() == col_name.lower()), None)
            if not target_col:
                new_cols = [c for c in df_result.columns if str(c) not in orig_cols]
                target_col = new_cols[0] if new_cols else None

            if not target_col:
                yield 'data: ' + _json.dumps({'type': 'error', 'message': 'Агент не создал нужный столбец'}) + '\n\n'
                return

            values = []
            for v in df_result[target_col]:
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    values.append(None)
                elif hasattr(v, 'isoformat'):
                    values.append(str(v))
                else:
                    values.append(v)

            # Stream in ~20 chunks for reactive visual effect
            chunk_size = max(1, math.ceil(len(values) / 20))
            for i in range(0, len(values), chunk_size):
                chunk = values[i:i + chunk_size]
                yield 'data: ' + _json.dumps({'type': 'chunk', 'start': i, 'values': chunk, 'total': len(values)}) + '\n\n'

            yield 'data: ' + _json.dumps({'type': 'done', 'total': len(values), 'text': result.get('text', '')}) + '\n\n'

        except Exception as e:
            logger.exception('db_excel_fill_sse failed')
            yield 'data: ' + _json.dumps({'type': 'error', 'message': str(e)}) + '\n\n'
        finally:
            for p in [tmp_path, result_path]:
                if p:
                    try:
                        os.unlink(str(p))
                    except Exception:
                        pass

    from django.http import StreamingHttpResponse
    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@site_login_required
@csrf_exempt
def db_excel_col_chat(request):
    """
    Planner endpoint for column chat.
    Determines if user's request has enough info to fill a column,
    or asks ONE clarifying question with option chips.

    Request JSON: {col_name, message, history: [{role, content}], columns, rows_sample}
    Response JSON:
      {ready: true,  prompt: "..."}          — enough info, use prompt to fill
      {ready: false, question: "...", options: ["A","B","C"]}  — need clarification
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    col_name    = data.get('col_name', '').strip()
    message     = data.get('message', '').strip()
    history     = data.get('history', [])       # [{role, content}]
    columns     = data.get('columns', [])[:20]
    rows_sample = data.get('rows_sample', [])[:3]

    if not col_name or not message:
        return JsonResponse({'error': 'col_name и message обязательны'}, status=400)

    history_text = ''
    if history:
        history_text = '\nИстория диалога:\n' + '\n'.join(
            f"{'Пользователь' if m['role']=='user' else 'Ассистент'}: {m['content']}"
            for m in history
        ) + '\n'

    import json as _json
    rows_preview = _json.dumps(rows_sample[:2], ensure_ascii=False)[:600] if rows_sample else ''

    # Load full DB schema (cached)
    schema_ctx = ''
    try:
        from chemico_agent.knowledge import get_wiki_context_cached
        schema_ctx = get_wiki_context_cached()
        if not schema_ctx:
            from chemico_agent.knowledge import BUILTIN_SCHEMA_DOCS
            schema_ctx = BUILTIN_SCHEMA_DOCS
    except Exception:
        pass

    planner_prompt = f"""Ты эксперт по базе данных Chemico (PostgreSQL) и помогаешь заполнять Excel-таблицы данными из неё.
Ты понимаешь свободные описания пользователя и переводишь их в точные SQL-инструкции.

{'=== СХЕМА БД Chemico ===' + chr(10) + schema_ctx + chr(10) if schema_ctx else ''}=== EXCEL-ТАБЛИЦА ===
Столбец для заполнения: «{col_name}»
Другие столбцы: {', '.join(columns)}
{f'Пример строк: {rows_preview}' if rows_preview else ''}
{history_text}=== НОВОЕ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ ===
{message}

=== ЗАДАЧА ===
Пользователь описывает своими словами что хочет. Ты должен понять его намерение и определить:
1. Понятно ЧТО достать из БД (поле, таблица, условия)
2. Понятно КАК сопоставить строки Excel с БД (ключевой столбец для JOIN)
3. Нет неоднозначности (если есть — задай ОДИН уточняющий вопрос)

Если всё ясно — верни JSON:
{{"ready": true, "prompt": "<точная инструкция агенту: что взять, из каких таблиц, по какому ключу, как обработать>"}}

Если нужно уточнение — задай ОДИН короткий вопрос с вариантами:
{{"ready": false, "question": "<короткий вопрос>", "options": ["Вариант А", "Вариант Б", "Вариант В"]}}

ВАЖНО: используй знания схемы БД для интерпретации — например «номер сделки» = registration_number, «покупатель» = company_unit через SOLD productunit, «поставщик» = shipper_company через providerunit.
Отвечай ТОЛЬКО валидным JSON без markdown-блоков."""

    try:
        from chemico_agent.llm import get_langchain_llm
        llm = get_langchain_llm(temperature=0)
        resp = llm.invoke(planner_prompt)
        content = resp.content.strip()
        # Strip markdown fences if LLM wrapped them
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
        plan = _json.loads(content)
        return JsonResponse(plan)
    except Exception as e:
        logger.exception('db_excel_col_chat planner failed')
        # Fallback: treat as ready with raw message
        return JsonResponse({'ready': True, 'prompt': message})


@site_login_required
@csrf_exempt
def db_excel_templates(request):
    import json as _json, uuid as _uuid
    from wiki_kb.models import WikiArticle, WikiRevision
    user = get_current_user(request)
    space = getattr(user, 'space', None)

    if request.method == 'GET':
        qs = WikiArticle.objects.filter(
            is_personal=True, is_deleted=False,
            created_by=user, slug__startswith='xl-tpl-',
        )
        result = []
        for art in qs.order_by('-updated_at')[:30]:
            try:
                config = _json.loads(art.content)
                result.append({
                    'slug': art.slug,
                    'name': art.title.replace('XL: ', '', 1),
                    'config': config,
                    'updated_at': art.updated_at.isoformat(),
                })
            except Exception:
                pass
        return JsonResponse({'templates': result})

    if request.method == 'POST':
        try:
            data = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)
        name = (data.get('name') or '').strip()
        config = data.get('config', {})
        if not name:
            return JsonResponse({'error': 'name required'}, status=400)
        content = _json.dumps(config, ensure_ascii=False, indent=2)
        base_slug = 'xl-tpl-' + (slugify(name) or 'template')
        existing = WikiArticle.objects.filter(
            slug__startswith=base_slug, created_by=user,
            is_personal=True, is_deleted=False,
        ).first()
        if existing:
            existing.content = content
            existing.title = f'XL: {name}'
            existing.save(update_fields=['content', 'title', 'updated_at'])
            WikiRevision.objects.create(article=existing, content=content, comment='Excel-студия')
            return JsonResponse({'ok': True, 'slug': existing.slug})
        slug = base_slug + '-' + _uuid.uuid4().hex[:6]
        art = WikiArticle.objects.create(
            title=f'XL: {name}', slug=slug, content=content,
            space=space, created_by=user, is_personal=True,
        )
        WikiRevision.objects.create(article=art, content=content, comment='Excel-студия')
        return JsonResponse({'ok': True, 'slug': slug}, status=201)

    return JsonResponse({'error': 'method not allowed'}, status=405)


# ── Excel Session (Telegram → Studio) ─────────────────────────────────────────

@site_login_required
def db_excel_session(request, session_id):
    """Загрузить сессию Excel Studio по UUID."""
    from .models import ExcelSession
    import json as _json
    user = get_current_user(request)
    try:
        session = ExcelSession.objects.get(id=session_id)
    except ExcelSession.DoesNotExist:
        return render(request, 'recordings/db_excel.html', {
            'user': user, 'session_error': 'Сессия не найдена',
        })
    return render(request, 'recordings/db_excel.html', {
        'user': user,
        'session_id': str(session.id),
        'session_json': _json.dumps({
            'filename': session.filename,
            'columns': session.columns,
            'rows': session.rows,
            'col_configs': session.col_configs,
        }),
    })


@site_login_required
@csrf_exempt
def db_excel_session_save(request, session_id):
    """Сохранить состояние сессии (rows + col_configs)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    import json as _json
    from .models import ExcelSession
    try:
        session = ExcelSession.objects.get(id=session_id)
    except ExcelSession.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    if 'rows' in data:
        session.rows = data['rows']
    if 'columns' in data:
        session.columns = data['columns']
    if 'col_configs' in data:
        session.col_configs = data['col_configs']
    session.save(update_fields=['rows', 'columns', 'col_configs', 'updated_at'])
    return JsonResponse({'ok': True})


@site_login_required
@csrf_exempt
def db_excel_wiki_templates(request):
    """
    GET             — список шаблонов из вики (chemico-tpl-*, xl-tpl-*, XL:, Шаблон:).
    GET ?slug=<slug> — детальный конфиг одного шаблона (slug обязателен).
    GET ?q=<query>  — поиск по slug/title для автодополнения.
    """
    from wiki_kb.models import WikiArticle
    import json as _json
    user = get_current_user(request)
    space = getattr(user, 'space', None)

    # ── Детальный запрос одной статьи ─────────────────────────────────────
    slug = request.GET.get('slug', '').strip()
    if slug:
        try:
            art = WikiArticle.objects.get(slug=slug, is_deleted=False)
        except WikiArticle.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        col_configs = {}
        content = art.content.strip() if art.content else ''
        if content.startswith('{'):
            try:
                col_configs = _json.loads(content)
            except Exception:
                pass
        return JsonResponse({
            'slug': art.slug,
            'title': art.title,
            'col_configs': col_configs,
            'has_configs': bool(col_configs),
            'content_preview': content[:1000],
            'url': f'/kb/{art.slug}/',
        })

    # ── Поиск для автодополнения ───────────────────────────────────────────
    from django.db.models import Q as _Q
    q = request.GET.get('q', '').strip()
    if q:
        qs = WikiArticle.objects.filter(is_deleted=False).filter(
            _Q(slug__icontains=q) | _Q(title__icontains=q)
        )
        if space:
            qs = qs.filter(_Q(space=space) | _Q(space__isnull=True))
        results = [{'slug': a.slug, 'title': a.title} for a in qs.order_by('-updated_at')[:20]]
        return JsonResponse({'results': results})

    # ── Список шаблонов ────────────────────────────────────────────────────
    qs = WikiArticle.objects.filter(
        is_deleted=False,
    ).filter(
        _Q(slug__startswith='chemico-tpl-') |
        _Q(slug__startswith='xl-tpl-') |
        _Q(title__startswith='XL: ') |
        _Q(title__startswith='Шаблон:') |
        _Q(title__startswith='Шаблон ') |
        _Q(slug='chemico-query-templates')
    )
    if space:
        qs = qs.filter(_Q(space=space) | _Q(space__isnull=True))

    result = []
    for art in qs.order_by('-updated_at')[:40]:
        entry = {
            'slug': art.slug,
            'title': art.title,
            'updated_at': art.updated_at.isoformat(),
            'url': f'/kb/{art.slug}/',
        }
        content = (art.content or '').strip()
        if content.startswith('{'):
            try:
                cfg = _json.loads(content)
                entry['col_configs'] = cfg
            except Exception:
                pass
        result.append(entry)
    return JsonResponse({'templates': result})


# ── MicroPreset API ─────────────────────────────────────────────────────────

@site_login_required
@csrf_exempt
def api_micropresets(request):
    """
    GET             — список пресетов пользователя
    POST            — создать или обновить пресет {name, wiki_slug, col_configs, id?}
    DELETE ?id=<n>  — удалить пресет
    """
    import json as _json
    from .models import MicroPreset
    user = get_current_user(request)

    if request.method == 'GET':
        presets = MicroPreset.objects.filter(user=user)
        return JsonResponse({'presets': [
            {
                'id': p.id,
                'name': p.name,
                'wiki_slug': p.wiki_slug,
                'col_configs': p.col_configs,
                'updated_at': p.updated_at.isoformat(),
                'col_count': len(p.col_configs),
            }
            for p in presets
        ]})

    if request.method == 'POST':
        try:
            data = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'name required'}, status=400)
        preset_id = data.get('id')
        if preset_id:
            try:
                preset = MicroPreset.objects.get(id=preset_id, user=user)
            except MicroPreset.DoesNotExist:
                preset = MicroPreset(user=user)
        else:
            preset = MicroPreset(user=user)
        preset.name = name
        preset.wiki_slug = data.get('wiki_slug', '')
        preset.col_configs = data.get('col_configs', {})
        preset.save()
        return JsonResponse({'ok': True, 'id': preset.id})

    if request.method == 'DELETE':
        preset_id = request.GET.get('id')
        if not preset_id:
            return JsonResponse({'error': 'id required'}, status=400)
        deleted, _ = MicroPreset.objects.filter(id=preset_id, user=user).delete()
        return JsonResponse({'ok': bool(deleted)})

    return JsonResponse({'error': 'method not allowed'}, status=405)


# ── Global chat planner ─────────────────────────────────────────────────────

@site_login_required
@csrf_exempt
def db_excel_global_chat(request):
    """
    Planner for global table fill (all columns at once).

    Request JSON: {message, columns, rows_sample, wiki_slug, history}
    Response JSON:
      {ready: true, plan: [{col, prompt, key_col}], summary: "..."}
      {ready: false, question: "...", options: [...]}
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    message     = data.get('message', '').strip()
    columns     = data.get('columns', [])[:30]
    rows_sample = data.get('rows_sample', [])[:3]
    wiki_slug   = data.get('wiki_slug', '').strip()
    history     = data.get('history', [])

    # message is optional — empty message means "auto-analyze"
    auto_mode = not message
    if auto_mode:
        message = 'Проанализируй столбцы таблицы и определи, какие из них можно заполнить из БД Chemico.'

    # Load DB schema for the planner (cached, full schema)
    try:
        from chemico_agent.knowledge import get_wiki_context_cached, BUILTIN_SCHEMA_DOCS
        schema_context = get_wiki_context_cached() or BUILTIN_SCHEMA_DOCS
    except Exception:
        schema_context = ''

    # Load extra wiki context if provided
    wiki_context = ''
    if wiki_slug:
        try:
            from wiki_kb.models import WikiArticle
            art = WikiArticle.objects.get(slug=wiki_slug, is_deleted=False)
            wiki_context = f'\nДополнительный контекст из вики «{art.title}»:\n{art.content[:1500]}\n'
        except Exception:
            pass

    rows_preview = _json.dumps(rows_sample[:3], ensure_ascii=False)[:800] if rows_sample else ''
    history_text = ''
    if history:
        history_text = '\nИстория диалога:\n' + '\n'.join(
            f"{'Пользователь' if m['role']=='user' else 'Ассистент'}: {m['content']}"
            for m in history[-6:]
        ) + '\n'

    cols_str = ', '.join(f'«{c}»' for c in columns)

    planner_prompt = f"""Ты эксперт по базе данных Chemico (PostgreSQL) и помогаешь заполнять Excel-таблицы данными из неё.
Ты понимаешь свободные описания пользователя и переводишь их в точные инструкции.

{'=== СХЕМА БД Chemico ===' + chr(10) + schema_context if schema_context else ''}
{wiki_context}
=== EXCEL-ТАБЛИЦА ===
Столбцы: {cols_str}
{f'Пример строк (первые 3):{chr(10)}{rows_preview}' if rows_preview else ''}
{history_text}
=== ЗАДАЧА ===
{message}

По названиям столбцов Excel и примерам данных определи:
1. Какой столбец является КЛЮЧОМ для соединения с БД (обычно: номер сделки, регистрационный номер, артикул)
2. Какие остальные столбцы можно заполнить данными из БД (сопоставь по смыслу с полями таблиц Chemico)
3. Для каждого заполняемого столбца — точная инструкция агенту

Верни JSON (без markdown-блоков):
{{"ready": true, "plan": [{{"col": "<имя столбца из Excel>", "prompt": "<точная инструкция: что достать, из какой таблицы, по какому полю сопоставить с ключом>", "key_col": "<имя ключевого столбца из Excel>"}}], "summary": "<1 предложение: что будет заполнено и по какому ключу>"}}

Если нет ни одного столбца который можно заполнить — верни:
{{"ready": false, "question": "<вопрос пользователю>", "options": ["вариант 1", "вариант 2"]}}

ВАЖНО:
- Не включай ключевой столбец сам в план (он используется только для JOIN)
- Если столбец уже содержит данные (ID, имена введённые вручную) — пропусти
- Отвечай ТОЛЬКО валидным JSON"""

    try:
        from chemico_agent.llm import get_langchain_llm
        llm = get_langchain_llm(temperature=0)
        resp = llm.invoke(planner_prompt)
        content = resp.content.strip()
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
        plan = _json.loads(content)
        return JsonResponse(plan)
    except Exception as e:
        logger.exception('db_excel_global_chat planner failed')
        return JsonResponse({'ready': True, 'plan': [], 'summary': 'Ошибка планировщика: ' + str(e)})


# ── Global fill SSE (multi-column) ──────────────────────────────────────────

@site_login_required
@csrf_exempt
def db_excel_global_fill_sse(request):
    """
    SSE multi-column fill. Executes fill for each column in the plan sequentially.

    Request JSON: {rows, plan: [{col, prompt, key_col}]}
    SSE events:
      {type: 'col_start',    col, index, total_cols}
      {type: 'col_progress', col, message}
      {type: 'col_chunk',    col, start, values, total}
      {type: 'col_done',     col, total}
      {type: 'col_error',    col, message}
      {type: 'done'}
    """
    if request.method != 'POST':
        from django.http import HttpResponse
        return HttpResponse('POST only', status=405)

    import json as _json, pandas as pd, tempfile, os, math
    try:
        data = _json.loads(request.body)
    except Exception:
        from django.http import HttpResponse
        return HttpResponse('bad json', status=400)

    rows = data.get('rows', [])
    plan = data.get('plan', [])  # [{col, prompt, key_col}]

    def generate():
        from chemico_agent.excel_input import enrich_excel_from_db
        total_cols = len(plan)

        for idx, item in enumerate(plan):
            col_name = item.get('col', '').strip()
            prompt   = item.get('prompt', '').strip()
            key_col  = item.get('key_col', '').strip()
            if not col_name or not prompt:
                continue

            yield 'data: ' + _json.dumps({'type': 'col_start', 'col': col_name, 'index': idx, 'total_cols': total_cols}) + '\n\n'
            yield 'data: ' + _json.dumps({'type': 'col_progress', 'col': col_name, 'message': f'⏳ Заполняю «{col_name}»...'}) + '\n\n'

            tmp_path = result_path = None
            try:
                df = pd.DataFrame(rows)
                orig_cols = set(str(c) for c in df.columns)
                if col_name in df.columns:
                    df = df.drop(columns=[col_name])

                tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
                tmp.close()
                tmp_path = tmp.name
                df.to_excel(tmp_path, index=False)

                instruction = f'Добавь колонку «{col_name}»: {prompt}.'
                if key_col:
                    instruction += f' Для сопоставления строк используй поле «{key_col}».'
                instruction += ' Сохрани все исходные строки, добавив только запрошенную колонку.'

                result = enrich_excel_from_db(tmp_path, instruction)
                result_path = result.get('excel_path')

                if not result_path or not os.path.exists(str(result_path)):
                    yield 'data: ' + _json.dumps({'type': 'col_error', 'col': col_name, 'message': result.get('text', 'Агент не вернул файл')}) + '\n\n'
                    continue

                df_result = pd.read_excel(result_path, engine='openpyxl')
                target_col = next((c for c in df_result.columns if str(c) == col_name), None)
                if not target_col:
                    target_col = next((c for c in df_result.columns if str(c).lower() == col_name.lower()), None)
                if not target_col:
                    new_cols = [c for c in df_result.columns if str(c) not in orig_cols]
                    target_col = new_cols[0] if new_cols else None

                if not target_col:
                    yield 'data: ' + _json.dumps({'type': 'col_error', 'col': col_name, 'message': 'Агент не создал нужный столбец'}) + '\n\n'
                    continue

                values = []
                for v in df_result[target_col]:
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        values.append(None)
                    elif hasattr(v, 'isoformat'):
                        values.append(str(v))
                    else:
                        values.append(v)

                # Stream in chunks
                chunk_size = max(1, math.ceil(len(values) / 20))
                for i in range(0, len(values), chunk_size):
                    chunk = values[i:i + chunk_size]
                    yield 'data: ' + _json.dumps({'type': 'col_chunk', 'col': col_name, 'start': i, 'values': chunk, 'total': len(values)}) + '\n\n'

                yield 'data: ' + _json.dumps({'type': 'col_done', 'col': col_name, 'total': len(values)}) + '\n\n'

                # Update rows in memory for subsequent columns (key_col match)
                for i, v in enumerate(values):
                    if i < len(rows):
                        rows[i][col_name] = v

            except Exception as e:
                logger.exception(f'db_excel_global_fill_sse failed for col {col_name}')
                yield 'data: ' + _json.dumps({'type': 'col_error', 'col': col_name, 'message': str(e)}) + '\n\n'
            finally:
                for p in [tmp_path, result_path]:
                    if p:
                        try:
                            os.unlink(str(p))
                        except Exception:
                            pass

        yield 'data: ' + _json.dumps({'type': 'done'}) + '\n\n'

    from django.http import StreamingHttpResponse
    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


# ── Space Chat (Supabase) ────────────────────────────────────────────────────

@site_login_required
def space_chat(request):
    """Страница чата пространства."""
    user = get_current_user(request)
    from .supabase_chat import is_configured
    return render(request, 'recordings/space_chat.html', {
        'current_user': user,
        'chat_configured': is_configured(),
    })


@site_login_required
@csrf_exempt
@require_http_methods(['POST'])
def space_chat_send(request):
    """Отправить сообщение."""
    import json as _json
    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'error': 'no space'}, status=403)
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)
    message = (data.get('message') or '').strip()
    if not message or len(message) > 4000:
        return JsonResponse({'error': 'empty or too long'}, status=400)
    from .supabase_chat import send_message
    result = send_message(
        space_slug=user.space.slug,
        user_email=user.email,
        display_name=user.display_name or user.email.split('@')[0],
        tg_username=user.tg_username or '',
        message=message,
    )
    if result is None:
        return JsonResponse({'error': 'supabase error'}, status=500)
    msg = result[0] if isinstance(result, list) else result
    return JsonResponse({'ok': True, 'id': msg.get('id'), 'created_at': msg.get('created_at')})


@site_login_required
def space_chat_messages(request):
    """JSON: список сообщений (polling)."""
    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'messages': []})
    after_id = int(request.GET.get('after', 0) or 0)
    from .supabase_chat import fetch_messages
    msgs = fetch_messages(user.space.slug, limit=80, after_id=after_id)
    return JsonResponse({'messages': msgs})


@site_login_required
def space_chat_stream(request):
    """SSE: real-time стрим новых сообщений (long-polling через SSE)."""
    import time
    user = get_current_user(request)
    if not user or not user.space:
        def empty():
            yield 'data: {}\n\n'
        from django.http import StreamingHttpResponse
        return StreamingHttpResponse(empty(), content_type='text/event-stream')

    space_slug = user.space.slug
    last_id = int(request.GET.get('after', 0) or 0)

    def generate():
        nonlocal last_id
        from .supabase_chat import fetch_messages
        import json as _json
        deadline = time.time() + 25  # 25s max connection
        while time.time() < deadline:
            msgs = fetch_messages(space_slug, limit=20, after_id=last_id)
            if msgs:
                for m in msgs:
                    yield f'data: {_json.dumps(m, ensure_ascii=False)}\n\n'
                    last_id = max(last_id, m.get('id', last_id))
            else:
                yield ': ping\n\n'
            time.sleep(2)

    from django.http import StreamingHttpResponse
    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


# ── Direct Messages (тет-а-тет) ────────────────────────────────────────────

@site_login_required
def dms_index(request):
    """Список участников пространства для переписки."""
    me = get_current_user(request)
    if not me or not me.space:
        return render(request, 'recordings/dms_index.html', {'contacts': []})

    contacts = SiteUser.objects.filter(space=me.space).exclude(pk=me.pk)
    now = timezone.now()

    from django.db.models import Q
    import datetime as _dt

    def _last_seen_str(ls):
        if not ls:
            return 'не был(а) в сети'
        delta = now - ls
        s = int(delta.total_seconds())
        if s < 180:
            return 'онлайн'
        if s < 3600:
            return f'был(а) {s // 60} мин назад'
        if s < 86400:
            return f'был(а) {s // 3600} ч назад'
        if s < 172800:
            return 'был(а) вчера'
        return f'был(а) {ls.strftime("%-d %b")}'

    contact_data = []
    for c in contacts:
        qs = DirectMessage.objects.filter(
            Q(sender=me, recipient=c) | Q(sender=c, recipient=me)
        )
        last_msg = qs.order_by('-created_at').first()
        total = qs.count()
        unread = DirectMessage.objects.filter(sender=c, recipient=me, read_at__isnull=True).count()
        is_online = bool(c.last_seen and (now - c.last_seen).total_seconds() < 180)
        contact_data.append({
            'user': c,
            'last_msg': last_msg,
            'total': total,
            'unread': unread,
            'online': is_online,
            'last_seen_str': _last_seen_str(c.last_seen),
        })

    # Сортировка: сначала непрочитанные, затем по дате последнего сообщения
    contact_data.sort(key=lambda x: (
        0 if x['unread'] else 1,
        -(x['last_msg'].created_at.timestamp() if x['last_msg'] else 0),
    ))

    return render(request, 'recordings/dms_index.html', {'contacts': contact_data})


@site_login_required
def dm_conversation(request, user_id):
    """Переписка с конкретным участником."""
    me = get_current_user(request)
    other = get_object_or_404(SiteUser, pk=user_id)
    if not me:
        return redirect('recordings:login')

    from django.db.models import Q
    qs = DirectMessage.objects.filter(
        Q(sender=me, recipient=other) | Q(sender=other, recipient=me)
    )
    msgs = qs.order_by('created_at')
    total = qs.count()

    DirectMessage.objects.filter(sender=other, recipient=me, read_at__isnull=True).update(read_at=timezone.now())

    return render(request, 'recordings/dm_conversation.html', {
        'other': other,
        'chat_msgs': msgs,
        'total': total,
    })


@site_login_required
def dm_send(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    me = get_current_user(request)
    other = get_object_or_404(SiteUser, pk=user_id)
    if not me:
        return JsonResponse({'ok': False, 'error': 'not auth'}, status=401)

    try:
        body = json.loads(request.body)
        text = (body.get('text') or '').strip()
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad json'}, status=400)

    if not text:
        return JsonResponse({'ok': False, 'error': 'empty'})

    msg = DirectMessage.objects.create(sender=me, recipient=other, space=me.space, text=text)

    # Уведомление получателю в Telegram
    if other.tg_chat_id:
        try:
            from .telegram_service import send_message as tg_send
            site_url = getattr(settings, 'SITE_URL', 'https://baza.business-pad.com')
            chat_link = f'{site_url}/dms/{me.pk}/'
            sender_name = me.display_name or me.email.split('@')[0]
            preview = text if len(text) <= 120 else text[:120] + '…'
            tg_text = (
                f'✉️ *{sender_name}* написал вам:\n'
                f'{preview}\n\n'
                f'[Открыть переписку]({chat_link})'
            )
            tg_send(other.tg_chat_id, tg_text)
        except Exception:
            pass

    return JsonResponse({'ok': True, 'id': msg.pk, 'created_at': msg.created_at.isoformat()})


@site_login_required
def dm_messages(request, user_id):
    me = get_current_user(request)
    other = get_object_or_404(SiteUser, pk=user_id)
    if not me:
        return JsonResponse({'messages': []})

    after_id = int(request.GET.get('after', 0) or 0)
    from django.db.models import Q
    qs = DirectMessage.objects.filter(
        Q(sender=me, recipient=other) | Q(sender=other, recipient=me)
    )
    if after_id:
        qs = qs.filter(pk__gt=after_id)
    msgs = list(qs.order_by('created_at').values('id', 'sender_id', 'text', 'created_at'))

    DirectMessage.objects.filter(
        sender=other, recipient=me, read_at__isnull=True, pk__gt=after_id
    ).update(read_at=timezone.now())

    result = [{'id': m['id'], 'mine': m['sender_id'] == me.pk, 'text': m['text'], 'created_at': m['created_at'].isoformat()} for m in msgs]
    return JsonResponse({'messages': result})


@site_login_required
def dm_stream(request, user_id):
    me = get_current_user(request)
    other = get_object_or_404(SiteUser, pk=user_id)
    if not me:
        def _empty():
            yield 'data: {}\n\n'
        return StreamingHttpResponse(_empty(), content_type='text/event-stream')

    last_id = int(request.GET.get('after', 0) or 0)
    me_pk = me.pk
    other_pk = other.pk

    last_read_id_seen = [0]  # track last read receipt we emitted

    def generate():
        nonlocal last_id
        from django.db.models import Q
        import json as _json
        deadline = time.time() + 25
        while time.time() < deadline:
            sent_any = False
            # New messages
            qs = DirectMessage.objects.filter(
                Q(sender_id=me_pk, recipient_id=other_pk) |
                Q(sender_id=other_pk, recipient_id=me_pk)
            ).filter(pk__gt=last_id).order_by('created_at')
            msgs = list(qs.values('id', 'sender_id', 'text', 'created_at'))
            for m in msgs:
                payload = {'id': m['id'], 'mine': m['sender_id'] == me_pk, 'text': m['text'], 'created_at': m['created_at'].isoformat()}
                yield f'data: {_json.dumps(payload, ensure_ascii=False)}\n\n'
                last_id = max(last_id, m['id'])
                sent_any = True
            # Read receipts: check if recipient read my sent messages
            read_row = DirectMessage.objects.filter(
                sender_id=me_pk, recipient_id=other_pk, read_at__isnull=False
            ).order_by('-id').values('id', 'read_at').first()
            if read_row and read_row['id'] > last_read_id_seen[0]:
                last_read_id_seen[0] = read_row['id']
                receipt = {'type': 'read', 'last_read_id': read_row['id'], 'read_at': read_row['read_at'].isoformat()}
                yield f'data: {_json.dumps(receipt)}\n\n'
                sent_any = True
            if not sent_any:
                yield ': ping\n\n'
            time.sleep(2)

    resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@site_login_required
def api_ping(request):
    """Обновить last_seen текущего пользователя; вернуть статус другого."""
    me = get_current_user(request)
    if not me:
        return JsonResponse({'ok': False})
    SiteUser.objects.filter(pk=me.pk).update(last_seen=timezone.now())
    other_id = request.GET.get('user')
    result = {'ok': True}
    if other_id:
        try:
            other = SiteUser.objects.filter(pk=int(other_id)).values('last_seen').first()
            ls = other['last_seen'] if other else None
            result['last_seen'] = ls.isoformat() if ls else None
        except Exception:
            pass
    return JsonResponse(result)
