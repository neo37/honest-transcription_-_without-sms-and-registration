import re
import threading
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.db.models import Count
from .models import Recording, PollLog, Comment, OcrJob, AccessLog, ShareToken, TagDefinition, Space, SiteUser, OrgRegistration, MagicLoginToken, MascotLog, SystemConfig, MeetingRoom, BotChatHistory, CustomBot
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
        AccessLog.objects.create(
            username=username,
            ip=_get_client_ip(request),
            user_agent=ua[:500],
            os_name=_parse_os(ua),
            screen=request.session.get('screen_resolution', ''),
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
    """Список активных транскрибаций с прогрессом."""
    recs = Recording.objects.filter(status=Recording.Status.TRANSCRIBING).values(
        'id', 'filename', 'ai_title', 'transcription_progress', 'transcription_stage'
    )
    return JsonResponse({'items': list(recs)})


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


def smarty_login(request):
    """Landing + login для домена smarty.rest."""
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
            error = 'Введите email и пароль. / Please enter email and password.'
        else:
            try:
                user = SiteUser.objects.select_related('space').get(
                    email__iexact=email,
                    space__slug=SMARTY_SPACE_SLUG,
                )
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
                    error = 'Неверный пароль. / Invalid password.'
            except SiteUser.DoesNotExist:
                error = 'Пользователь не найден. / User not found.'
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

    current_user = get_current_user(request)
    return render(request, 'recordings/recording_detail.html', {
        'recording': rec,
        'comments': comments,
        'download_url': download_url,
        'speaker_profiles': sorted(all_names),
        'auto_names_json': _json.dumps(auto_names, ensure_ascii=False),
        'current_user': current_user,
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
    device = request.POST.get('device', 'auto')          # auto | cpu | gpu

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
    quality = request.POST.get('quality')
    lang = request.POST.get('language')
    update_fields = []
    if quality in dict(Recording.QUALITY_CHOICES):
        rec.transcription_quality = quality
        update_fields.append('transcription_quality')
    if lang in dict(Recording.LANGUAGE_CHOICES):
        rec.transcription_language = lang
        update_fields.append('transcription_language')

    # Сохраняем device override в SystemConfig (временно, до окончания транскрипции)
    if device in ('cpu', 'gpu'):
        SystemConfig.set(f'device_override_{rec.pk}', device)

    rec.transcription = ''
    rec.status = Recording.Status.STABLE
    update_fields.extend(['transcription', 'status'])
    rec.save(update_fields=update_fields)

    enqueue_transcribe(rec, priority=1)

    def _run_one():
        try:
            services.process_one_transcribe()
        except Exception:
            pass
    threading.Thread(target=_run_one, daemon=True).start()
    messages.success(request, f'Транскрибация для «{rec.filename}» поставлена в очередь.')
    return redirect(next_url)


@site_login_required
def download_recording(request, recording_id):
    """Редирект на временную ссылку скачивания MP3 из S3."""
    rec = get_object_or_404(Recording, pk=recording_id)
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
        rec = Recording.objects.create(
            s3_key=s3_key,
            filename=os.path.basename(safe_name),
            size_bytes=size,
            status=Recording.Status.STABLE,
            space=current_user.space if current_user else None,
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
    f = request.FILES.get('document')
    if not f:
        messages.warning(request, 'Выберите файл (PDF или изображение).')
        return redirect(reverse('recordings:ocr'))
    _mime_ext = {
        'image/jpeg': '.jpg', 'image/jpg': '.jpg',
        'image/png': '.png', 'application/pdf': '.pdf',
    }
    suffix = os.path.splitext(f.name or '')[1].lower()
    if not suffix:
        suffix = _mime_ext.get((f.content_type or '').split(';')[0].strip().lower(), '.bin')
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        for chunk in f.chunks():
            tmp.write(chunk)
        path = tmp.name
    ocr_method = request.POST.get('method', 'auto')
    if ocr_method not in ('auto', 'tesseract', 'olmocr'):
        ocr_method = 'auto'
    job = OcrJob.objects.create(
        original_filename=f.name or 'document',
        file_path=path,
        status='pending',
        space=current_user.space if current_user else None,
    )
    _run_ocr_job(job, method=ocr_method)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    job.file_path = ''
    job.save(update_fields=['file_path'])
    return redirect(reverse('recordings:ocr_job_detail', args=[job.id]))


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
            from .models import Recording as _Rec
            from .queue_services import enqueue_transcribe as _enqueue
            stuck = list(_Rec.objects.filter(status=_Rec.Status.TRANSCRIBING))
            for r in stuck:
                r.status = _Rec.Status.STABLE
                r.transcription_progress = 0
                r.transcription_stage = ''
                r.save(update_fields=['status', 'transcription_progress', 'transcription_stage'])
                _enqueue(r, priority=1)
        elif key:
            SystemConfig.set(key, value)
            if key == 'ocr_gpu_mode':
                _write_ocr_gpu_flag(value == '1')
        return redirect('recordings:system_config')

    ocr_gpu_mode = SystemConfig.get('ocr_gpu_mode', '0') == '1'

    from .models import TranscribeQueue, EmbeddingQueue
    transcribing_now = Recording.objects.filter(status=Recording.Status.TRANSCRIBING).order_by('-updated_at')
    transcribe_queue = TranscribeQueue.objects.select_related('recording').order_by('-priority', 'created_at')
    embedding_queue = EmbeddingQueue.objects.select_related('recording').order_by('created_at')

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

    return render(request, 'recordings/system_config.html', {
        'ocr_gpu_mode': ocr_gpu_mode,
        'lk_config': lk_config,
        'lk_yaml_error': lk_yaml_error,
        'lk_url': getattr(django_settings, 'LIVEKIT_URL', ''),
        'lk_api_key': _mask(getattr(django_settings, 'LIVEKIT_API_KEY', '')),
        'transcribing_now': transcribing_now,
        'transcribe_queue': transcribe_queue,
        'embedding_queue': embedding_queue,
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
    return render(request, 'recordings/meetings.html', {'meetings': qs})


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
    from django.utils.text import slugify
    base_slug = slugify(title) or uuid.uuid4().hex[:12]
    room_name = base_slug
    suffix = 1
    while MeetingRoom.objects.filter(room_name=room_name).exists():
        room_name = f'{base_slug}-{suffix}'
        suffix += 1
    meeting = MeetingRoom.objects.create(
        room_name=room_name,
        title=title,
        with_mascot=with_mascot,
        created_by=user,
        space=user.space if user else None,
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
    join_url = f'https://meet.business-pad.com/rooms/{room_name}'
    users = SiteUser.objects.filter(pk__in=user_ids, tg_chat_id__isnull=False)
    sent = 0
    for u in users:
        telegram_service.send_meeting_invite(u.tg_chat_id, meeting.title, join_url)
        sent += 1
    return JsonResponse({'ok': True, 'sent': sent})


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

    user = get_current_user(request)
    if not user or not user.space:
        return JsonResponse({'events': []})

    rooms = list(MeetingRoom.objects.filter(space=user.space).select_related('created_by'))
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

    # 1. Events from MeetingRoom entries
    for room in rooms:
        recs = sorted(room_rec_map.get(room.room_name, []), key=lambda x: x[0])
        start = room.created_at
        end = room.ended_at
        if not end:
            if recs:
                end = recs[-1][0] + timedelta(minutes=5)
            else:
                end = start + timedelta(minutes=60)

        events.append({
            'id': f'room-{room.pk}',
            'title': room.title or room.room_name,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'color': '#16a34a' if not room.ended_at else '#2563eb',
            'extendedProps': {
                'room_name': room.room_name,
                'room_url': f'/meetings/{room.room_name}/',
                'is_active': room.ended_at is None,
                'created_by': room.created_by.email if room.created_by else '',
                'recordings': [
                    {
                        'id': r.pk,
                        'title': r.ai_title or r.filename,
                        'url': f'/r/{r.pk}/',
                        'status': r.status,
                        'ts': ts.isoformat(),
                    }
                    for ts, r in recs
                ],
            },
        })

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
                'start': start.isoformat(),
                'end': end.isoformat(),
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
    """История переписок всех пользователей с ботами."""
    user = get_current_user(request)

    # Собираем все уникальные (chat_id, bot_id) пары для пространства
    # Определяем пространство: показываем только свои боты + главный бот пространства
    space = user.space

    # Пользователи пространства с их chat_id
    space_users = SiteUser.objects.filter(space=space, tg_chat_id__isnull=False).values('tg_chat_id', 'email')
    space_chat_ids = {u['tg_chat_id']: u['email'] for u in space_users}

    # Кастомные боты этого пространства
    space_bots = {b.pk: b for b in CustomBot.objects.filter(space=space, is_active=True)}

    # Все уникальные (chat_id, bot_id) из истории для этих chat_id
    convs_qs = (
        BotChatHistory.objects
        .filter(chat_id__in=list(space_chat_ids.keys()))
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
        bot_name = space_bots[bid].username if bid and bid in space_bots else 'Главный бот'
        conversations.append({
            'chat_id': cid,
            'bot_id': bid,
            'bot_id_str': str(bid) if bid is not None else '',
            'email': space_chat_ids.get(cid, f'TG {cid}'),
            'bot_name': bot_name,
            'last_at': last_msg.created_at if last_msg else None,
            'active': cid == selected_chat_id and bid == selected_bot_id_int,
        })

    # Сообщения выбранного разговора
    messages_list = []
    if selected_chat_id is not None:
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
        'space_bots': space_bots,
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
