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
from .models import Recording, PollLog, Comment, OcrJob, AccessLog, ShareToken, TagDefinition, Space, SiteUser, OrgRegistration, MagicLoginToken
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
                return redirect(reverse('recordings:index'))
    bp_emails = sorted(settings.BP_EMAILS)
    return render(request, 'recordings/login.html', {
        'error': error,
        'registered': registered,
        'bp_emails': bp_emails,
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

    fn_filters = [x for x in request.GET.getlist('fn') if x in ('cpq', 'dev', 'daily', 'bp', 'analytics', 'demo')]
    date_str = request.GET.get('date') or timezone.now().strftime('%Y-%m-%d')
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

            if fn_filters:
                q = Q()
                for s in fn_filters:
                    q |= Q(filename__icontains=s)
                qs = qs.filter(q)

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
        'tag_choices': tag_choices,
        'fn_filters': fn_filters,
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
    rec = get_object_or_404(Recording, pk=recording_id)
    _log_access(request, AccessLog.EVENT_VIEW, recording=rec)
    comments = list(rec.comments.all())
    download_url = None
    if rec.s3_key:
        try:
            download_url = get_presigned_download_url(rec.s3_key, rec.filename, expires_in=300)
        except Exception:
            pass
    return render(request, 'recordings/recording_detail.html', {
        'recording': rec,
        'comments': comments,
        'download_url': download_url,
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
    """Поставить транскрибацию в очередь с приоритетом; воркер подхватит. Позволяет выбрать качество и язык."""
    from .queue_services import enqueue_transcribe
    rec = get_object_or_404(Recording, pk=recording_id)
    
    quality = request.POST.get('quality')
    lang = request.POST.get('language')
    
    update_fields = []
    if quality in dict(Recording.QUALITY_CHOICES):
        rec.transcription_quality = quality
        update_fields.append('transcription_quality')
    if lang in dict(Recording.LANGUAGE_CHOICES):
        rec.transcription_language = lang
        update_fields.append('transcription_language')
    
    # Сбрасываем старую транскрипцию если она была
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
    thread = threading.Thread(target=_run_one, daemon=True)
    thread.start()
    messages.success(request, f'Транскрибация для «{rec.filename}» поставлена в очередь. Обновите страницу через минуту.')
    next_url = request.POST.get('next') or request.GET.get('next') or reverse('recordings:recording_detail', args=[rec.pk])
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
