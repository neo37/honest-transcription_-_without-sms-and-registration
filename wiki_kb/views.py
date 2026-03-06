import uuid
import markdown as md_lib
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.utils.text import slugify

from recordings.auth_backend import site_login_required, get_current_user
from recordings.models import Recording
from .models import WikiArticle, WikiRevision


def _render_md(text):
    md = md_lib.Markdown(extensions=['extra', 'toc', 'nl2br'])
    return md.convert(text)


def _get_tree(space):
    """Корневые статьи для боковой панели — только из пространства пользователя."""
    return WikiArticle.objects.filter(
        is_deleted=False, parent__isnull=True, space=space
    ).order_by('order', 'title')


def _wiki_qs(space):
    """Базовый queryset статей пространства пользователя."""
    return WikiArticle.objects.filter(is_deleted=False, space=space)


def _unique_slug(title):
    base = slugify(title) or 'article'
    slug = base
    n = 1
    while WikiArticle.objects.filter(slug=slug).exists():
        slug = f'{base}-{n}'
        n += 1
    return slug


def _get_article_choices(space, exclude_ids=None):
    """Плоский список (article, depth) для пикера иерархии — только пространство пользователя."""
    exclude_ids = set(exclude_ids or [])
    result = []

    def traverse(parent_id, depth):
        qs = WikiArticle.objects.filter(
            is_deleted=False, parent_id=parent_id, space=space
        ).exclude(pk__in=exclude_ids).order_by('order', 'title')
        for art in qs:
            result.append((art, depth))
            traverse(art.pk, depth + 1)

    traverse(None, 0)
    return result


def generate_wiki_content(recording):
    """Генерирует Markdown-содержимое статьи KB по транскрипции встречи через AI API."""
    from recordings.services import call_llm_api

    text = (recording.transcription or '').strip()
    if not text:
        return ''

    max_chars = 12000
    if len(text) > max_chars:
        first = text[:4000]
        mid_pos = len(text) // 2
        mid = text[mid_pos - 2000:mid_pos + 2000]
        last = text[-4000:]
        text_sample = f"[НАЧАЛО]\n{first}\n\n[СЕРЕДИНА]\n{mid}\n\n[КОНЕЦ]\n{last}"
    else:
        text_sample = text

    prompt = (
        "Ниже приведена транскрипция рабочей встречи. "
        "Напиши структурированную страницу базы знаний компании в формате Markdown на русском языке. "
        "Структура: краткое резюме встречи (2-4 предложения), ключевые решения и договорённости (список), "
        "задачи и ответственные (если упоминались, список), важные факты и цифры (если есть). "
        "Используй ## для разделов, - для списков, **жирный** для ключевых пунктов. "
        "Ответ — только Markdown без вводных фраз.\n\n"
        f"Транскрипция:\n{text_sample}"
    )

    clean = call_llm_api(
        prompt=prompt,
        session_id=f"wiki_rec_{recording.pk}",
        log_id=f"wiki_rec_{recording.pk}",
        timeout=120,
    )
    if clean.startswith('```markdown'):
        clean = clean.split('```markdown', 1)[1].rsplit('```', 1)[0].strip()
    elif clean.startswith('```'):
        clean = clean.split('```', 1)[1].rsplit('```', 1)[0].strip()
    return clean


@site_login_required
def kb_index(request):
    user = get_current_user(request)
    space = user.space if user else None
    roots = _get_tree(space)
    first = _wiki_qs(space).order_by('order', 'title').first()
    if first:
        return redirect('wiki_kb:article_detail', slug=first.slug)
    return render(request, 'wiki_kb/article_detail.html', {
        'roots': roots,
        'article': None,
        'content_html': '',
        'ancestors': [],
        'linked_recordings': [],
    })


@site_login_required
def article_detail(request, slug):
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)
    content_html = _render_md(article.content)
    roots = _get_tree(space)
    ancestors = article.get_ancestors()
    linked_recordings = article.recordings.all().order_by('-created_at')
    return render(request, 'wiki_kb/article_detail.html', {
        'roots': roots,
        'article': article,
        'content_html': content_html,
        'ancestors': ancestors,
        'linked_recordings': linked_recordings,
    })


@site_login_required
def article_create(request):
    user = get_current_user(request)
    space = user.space if user else None

    parent = None
    parent_slug = request.GET.get('parent')
    if parent_slug:
        parent = _wiki_qs(space).filter(slug=parent_slug).first()

    # OCR pre-fill
    prefill_title = ''
    prefill_content = ''
    ocr_job_id = request.GET.get('ocr_job')
    if ocr_job_id:
        try:
            from recordings.models import OcrJob
            prefill_ocr_job = OcrJob.objects.filter(pk=int(ocr_job_id), status='done').first()
            if prefill_ocr_job:
                prefill_title = prefill_ocr_job.original_filename
                prefill_content = prefill_ocr_job.result_markdown or ''
        except (ValueError, Exception):
            pass

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '')
        parent_id = request.POST.get('parent_id') or None

        if not title:
            roots = _get_tree(space)
            return render(request, 'wiki_kb/article_edit.html', {
                'roots': roots,
                'error': 'Заголовок обязателен',
                'form': {'title': title, 'content': content},
                'parent': parent,
                'is_create': True,
            })

        parent_obj = None
        if parent_id:
            parent_obj = _wiki_qs(space).filter(pk=parent_id).first()

        article = WikiArticle.objects.create(
            title=title,
            slug=_unique_slug(title),
            content=content,
            parent=parent_obj,
            space=space,
            created_by=user,
            updated_by=user,
        )
        WikiRevision.objects.create(article=article, content=content, revised_by=user)
        return redirect('wiki_kb:article_detail', slug=article.slug)

    roots = _get_tree(space)
    return render(request, 'wiki_kb/article_edit.html', {
        'roots': roots,
        'parent': parent,
        'form': {'title': prefill_title, 'content': prefill_content},
        'is_create': True,
    })


@site_login_required
def article_edit(request, slug):
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '')

        if not title:
            roots = _get_tree(space)
            return render(request, 'wiki_kb/article_edit.html', {
                'roots': roots,
                'article': article,
                'error': 'Заголовок обязателен',
                'form': {'title': title, 'content': content},
                'is_create': False,
            })

        WikiRevision.objects.create(
            article=article,
            content=article.content,
            revised_by=user,
            comment='до редактирования',
        )

        article.title = title
        article.content = content
        article.updated_by = user
        article.save()
        return redirect('wiki_kb:article_detail', slug=article.slug)

    roots = _get_tree(space)
    return render(request, 'wiki_kb/article_edit.html', {
        'roots': roots,
        'article': article,
        'form': {'title': article.title, 'content': article.content},
        'is_create': False,
    })


@site_login_required
def article_delete(request, slug):
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)

    if request.method == 'POST':
        article.is_deleted = True
        article.save()
        return redirect('wiki_kb:kb_index')

    roots = _get_tree(space)
    return render(request, 'wiki_kb/article_delete.html', {
        'roots': roots,
        'article': article,
    })


@site_login_required
def article_history(request, slug):
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)
    revisions = article.revisions.all()[:30]
    roots = _get_tree(space)
    return render(request, 'wiki_kb/article_history.html', {
        'roots': roots,
        'article': article,
        'revisions': revisions,
    })


@site_login_required
def article_share_toggle(request, slug):
    """Включить/выключить публичную ссылку (POST)."""
    if request.method != 'POST':
        return redirect('wiki_kb:article_detail', slug=slug)
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)
    if article.share_token:
        article.share_token = None
    else:
        article.share_token = uuid.uuid4()
    article.save(update_fields=['share_token'])
    return redirect('wiki_kb:article_detail', slug=slug)


@site_login_required
def article_pick_recordings(request, slug):
    """Страница выбора связанных записей для статьи."""
    from django.conf import settings
    from django.db.models import Q
    from recordings.models import Space

    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)

    bp_slug = getattr(settings, 'BP_SPACE_SLUG', 'org-bp')
    if user and user.space:
        if user.space.slug == bp_slug:
            other_space_ids = Space.objects.exclude(slug=bp_slug).values_list('id', flat=True)
            base_qs = Recording.objects.exclude(space_id__in=other_space_ids)
        else:
            base_qs = Recording.objects.filter(space=user.space)
    else:
        base_qs = Recording.objects.filter(space__isnull=True)

    q = request.GET.get('q', '').strip()
    date_str = request.GET.get('date', '').strip()
    tag_filter = request.GET.get('tag', '').strip()

    qs = base_qs.filter(status='done').order_by('-created_at')

    if q:
        qs = qs.filter(
            Q(ai_title__icontains=q) |
            Q(filename__icontains=q) |
            Q(transcription__icontains=q) |
            Q(ai_summary__icontains=q)
        )
    if date_str:
        qs = qs.filter(created_at__date=date_str)
    if tag_filter:
        qs = qs.filter(tag=tag_filter)

    recordings_qs = qs[:300]

    if request.method == 'POST':
        recording_ids = request.POST.getlist('recording_ids')
        recs = Recording.objects.filter(pk__in=[int(r) for r in recording_ids if r.isdigit()])
        article.recordings.set(recs)
        return redirect('wiki_kb:article_detail', slug=article.slug)

    selected_ids = set(article.recordings.values_list('pk', flat=True))
    tag_choices = Recording.TAG_CHOICES

    return render(request, 'wiki_kb/pick_recordings.html', {
        'article': article,
        'recordings': recordings_qs,
        'selected_ids': selected_ids,
        'q': q,
        'date_str': date_str,
        'tag_filter': tag_filter,
        'tag_choices': tag_choices,
    })


@site_login_required
def wiki_from_recording(request, recording_id):
    """Создать вики-статью из записи: выбор места в иерархии → создание + LLM-контент."""
    user = get_current_user(request)
    space = user.space if user else None
    recording = get_object_or_404(Recording, pk=recording_id)

    if request.method == 'POST':
        parent_id = request.POST.get('parent_id') or None
        parent = None
        if parent_id:
            parent = _wiki_qs(space).filter(pk=parent_id).first()

        title = recording.ai_title or recording.filename
        content = generate_wiki_content(recording)

        article = WikiArticle.objects.create(
            title=title,
            slug=_unique_slug(title),
            content=content,
            parent=parent,
            space=space,
            created_by=user,
            updated_by=user,
        )
        article.recordings.add(recording)
        WikiRevision.objects.create(article=article, content=content, revised_by=user, comment='создано из записи')
        return redirect('wiki_kb:article_detail', slug=article.slug)

    article_choices = _get_article_choices(space)
    return render(request, 'wiki_kb/hierarchy_picker.html', {
        'mode': 'create_from_recording',
        'recording': recording,
        'article_choices': article_choices,
        'submit_label': 'Создать страницу в базе знаний',
        'current_parent_id': '',
    })


@site_login_required
def article_move(request, slug):
    """Перенести статью в другое место иерархии."""
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)

    if request.method == 'POST':
        parent_id = request.POST.get('parent_id') or None
        if parent_id:
            parent = _wiki_qs(space).filter(pk=parent_id).first()
            if parent and parent.pk != article.pk:
                article.parent = parent
                article.save(update_fields=['parent'])
        else:
            article.parent = None
            article.save(update_fields=['parent'])
        return redirect('wiki_kb:article_detail', slug=article.slug)

    exclude_ids = [article.pk] + article.get_all_descendants_ids()
    article_choices = _get_article_choices(space, exclude_ids=exclude_ids)
    return render(request, 'wiki_kb/hierarchy_picker.html', {
        'mode': 'move',
        'article': article,
        'article_choices': article_choices,
        'submit_label': 'Перенести сюда',
        'current_parent_id': str(article.parent_id or ''),
    })


def generate_merged_content(source, target):
    """Объединяет содержимое двух статей через LLM. Возвращает (merged_content, error_message)."""
    from recordings.services import call_llm_api

    src_content = (source.content or '').strip()
    tgt_content = (target.content or '').strip()

    prompt = (
        f"Объедини содержимое двух страниц базы знаний в одну структурированную страницу в формате Markdown на русском языке.\n\n"
        f"Страница 1 — «{source.title}»:\n{src_content}\n\n"
        f"Страница 2 — «{target.title}»:\n{tgt_content}\n\n"
        "Создай единую, логически связную страницу базы знаний. "
        "Устрани дублирование, сохрани всю уникальную информацию, структурируй разделы. "
        "Используй ## для разделов, - для списков, **жирный** для ключевых пунктов. "
        "Ответ — только Markdown без вводных фраз."
    )

    result = call_llm_api(
        prompt=prompt,
        session_id=f"wiki_merge_{source.pk}_{target.pk}",
        log_id=f"wiki_merge_{source.pk}_{target.pk}",
        timeout=120,
    )

    if not result:
        return '', 'ИИ вернул пустой ответ. Проверьте настройки LLM API или объедините статьи вручную.'

    clean = result
    if clean.startswith('```markdown'):
        clean = clean.split('```markdown', 1)[1].rsplit('```', 1)[0].strip()
    elif clean.startswith('```'):
        clean = clean.split('```', 1)[1].rsplit('```', 1)[0].strip()
    return clean, ''


@site_login_required
def article_merge(request, slug):
    """Объединить эту статью с другой через LLM."""
    user = get_current_user(request)
    space = user.space if user else None
    article = get_object_or_404(_wiki_qs(space), slug=slug)

    if request.method == 'POST':
        target_id = request.POST.get('target_id') or None
        target = None
        if target_id:
            target = _wiki_qs(space).filter(pk=target_id).exclude(pk=article.pk).first()

        if not target:
            article_choices = _get_article_choices(space, exclude_ids=[article.pk])
            return render(request, 'wiki_kb/hierarchy_picker.html', {
                'mode': 'merge',
                'article': article,
                'article_choices': article_choices,
                'submit_label': 'Объединить',
                'merge_error': 'Выберите статью для объединения.',
            })

        merged_content, error = generate_merged_content(article, target)

        if error:
            article_choices = _get_article_choices(space, exclude_ids=[article.pk])
            return render(request, 'wiki_kb/hierarchy_picker.html', {
                'mode': 'merge',
                'article': article,
                'article_choices': article_choices,
                'submit_label': 'Попробовать снова',
                'merge_error': error,
                'merge_target': target,
            })

        target.content = merged_content
        target.updated_by = user
        target.save(update_fields=['content', 'updated_by', 'updated_at'])
        WikiRevision.objects.create(
            article=target,
            content=merged_content,
            revised_by=user,
            comment=f'объединено со статьёй «{article.title}»',
        )

        link_content = f'> Содержимое этой статьи объединено с [{target.title}](/kb/{target.slug}/)\n\nСм. [{target.title}](/kb/{target.slug}/)'
        article.content = link_content
        article.updated_by = user
        article.save(update_fields=['content', 'updated_by', 'updated_at'])
        WikiRevision.objects.create(
            article=article,
            content=link_content,
            revised_by=user,
            comment=f'объединено в статью «{target.title}»',
        )

        return redirect('wiki_kb:article_detail', slug=target.slug)

    article_choices = _get_article_choices(space, exclude_ids=[article.pk])
    return render(request, 'wiki_kb/hierarchy_picker.html', {
        'mode': 'merge',
        'article': article,
        'article_choices': article_choices,
        'submit_label': 'Объединить',
        'current_parent_id': '',
    })


@site_login_required
def wiki_search(request):
    """Поиск по базе знаний: только в пространстве текущего пользователя."""
    from django.db.models import Q
    user = get_current_user(request)
    space = user.space if user else None

    q = request.GET.get('q', '').strip()
    date_str = request.GET.get('date', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()

    results = []
    if q or date_str or date_to_str:
        qs = _wiki_qs(space)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))
        if date_str:
            from datetime import datetime as _dt
            try:
                qs = qs.filter(updated_at__date__gte=_dt.strptime(date_str, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to_str:
            from datetime import datetime as _dt
            try:
                qs = qs.filter(updated_at__date__lte=_dt.strptime(date_to_str, '%Y-%m-%d').date())
            except ValueError:
                pass
        results = list(qs.order_by('-updated_at')[:200])

    roots = _get_tree(space)
    return render(request, 'wiki_kb/wiki_search.html', {
        'roots': roots,
        'results': results,
        'q': q,
        'date_str': date_str,
        'date_to_str': date_to_str,
        'searched': bool(q or date_str or date_to_str),
    })


def article_public(request, token):
    """Публичный просмотр статьи без авторизации — фильтрация по пространству не нужна."""
    article = get_object_or_404(WikiArticle, share_token=token, is_deleted=False)
    content_html = _render_md(article.content)
    ancestors = article.get_ancestors()
    linked_recordings = article.recordings.all().order_by('-created_at')
    return render(request, 'wiki_kb/article_public.html', {
        'article': article,
        'content_html': content_html,
        'ancestors': ancestors,
        'linked_recordings': linked_recordings,
    })
