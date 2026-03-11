from django.contrib import admin
from django.shortcuts import render, redirect
from django.urls import path
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils.html import format_html

from .models import (
    Recording, PollLog, Comment, TranscribeQueue, EmbeddingQueue,
    TagDefinition, AccessLog, ShareToken, Space, SiteUser, AIConfig,
    SystemConfig, SpeakerProfile, OcrJob, MagicLoginToken, OrgRegistration,
    MascotLog, MascotTask, MeetingRoom, CustomBot, BotChatHistory, BotSetupState,
)
from .queue_services import enqueue_transcribe, enqueue_embedding
from .s3_client import delete_mp3_from_s3
from . import services


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ('filename', 'status', 'tag', 'size_bytes', 'created_at', 'transcribed_at')
    list_filter = ('status',)
    search_fields = ('filename', 's3_key')
    actions = [
        'admin_action_enqueue_embedding',
        'admin_action_enqueue_transcribe',
        'admin_action_generate_ai_summary',
        'admin_action_delete_and_remove_mp3',
    ]

    @admin.action(description='Запустить эмбеддинги для выбранных')
    def admin_action_enqueue_embedding(self, request, queryset):
        added = 0
        for rec in queryset:
            if rec.transcription and rec.transcription.strip():
                enqueue_embedding(rec)
                added += 1
        self.message_user(
            request,
            f'В очередь эмбеддингов добавлено записей: {added}. Без транскрипции пропущено: {queryset.count() - added}.',
            messages.SUCCESS,
        )

    @admin.action(description='Массовая транскрибация (в очередь)')
    def admin_action_enqueue_transcribe(self, request, queryset):
        recs = list(queryset.exclude(
            status__in=(Recording.Status.DONE, Recording.Status.TRANSCRIBING),
        ).order_by('created_at'))
        n = len(recs)
        # Первая запись получает наивысший приоритет n, последняя — 1
        for i, rec in enumerate(recs):
            enqueue_transcribe(rec, priority=n - i)
        self.message_user(
            request,
            f'В очередь транскрибации добавлено записей: {n}. Приоритеты: {n}…1.',
            messages.SUCCESS,
        )

    @admin.action(description='Сгенерировать AI название и саммари')
    def admin_action_generate_ai_summary(self, request, queryset):
        done = 0
        for rec in queryset:
            if rec.transcription and rec.transcription.strip():
                services.generate_ai_summary(rec)
                done += 1
        self.message_user(
            request,
            f'AI саммари сгенерировано для {done} записей. Без транскрипции пропущено: {queryset.count() - done}.',
            messages.SUCCESS,
        )

    @admin.action(description='Удалить запись и MP3 в S3')
    def admin_action_delete_and_remove_mp3(self, request, queryset):
        total = queryset.count()
        deleted_s3 = 0
        for rec in list(queryset):
            if rec.s3_key:
                if delete_mp3_from_s3(rec.s3_key):
                    deleted_s3 += 1
            rec.delete()
        self.message_user(
            request,
            f'Удалено записей: {total}. Файлов удалено в S3: {deleted_s3}.',
            messages.SUCCESS,
        )


@admin.register(PollLog)
class PollLogAdmin(admin.ModelAdmin):
    list_display = ('started_at', 'finished_at', 'files_found', 'files_stable', 'message', 'success')
    list_filter = ('success',)
    list_per_page = 50


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('recording', 'text_preview', 'created_at')
    list_filter = ('recording',)

    def text_preview(self, obj):
        return (obj.text[:50] + '…') if len(obj.text) > 50 else obj.text
    text_preview.short_description = 'Текст'


@admin.register(TranscribeQueue)
class TranscribeQueueAdmin(admin.ModelAdmin):
    list_display = ('recording', 'recording_status', 'priority', 'created_at')
    list_filter = ('priority',)
    search_fields = ('recording__filename',)
    ordering = ('-priority', 'created_at')
    raw_id_fields = ('recording',)
    actions = ['cancel_selected', 'move_to_top']

    def recording_status(self, obj):
        return obj.recording.get_status_display()
    recording_status.short_description = 'Статус записи'

    @admin.action(description='Отменить (удалить из очереди)')
    def cancel_selected(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'Удалено из очереди: {count}')

    @admin.action(description='Переместить в начало очереди (приоритет 999)')
    def move_to_top(self, request, queryset):
        count = queryset.update(priority=999)
        self.message_user(request, f'Приоритет повышен для {count} записей')


@admin.register(EmbeddingQueue)
class EmbeddingQueueAdmin(admin.ModelAdmin):
    list_display = ('recording', 'created_at')
    search_fields = ('recording__filename',)
    raw_id_fields = ('recording',)
    actions = ['cancel_selected']

    @admin.action(description='Отменить (удалить из очереди)')
    def cancel_selected(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'Удалено из очереди: {count}')


@admin.register(TagDefinition)
class TagDefinitionAdmin(admin.ModelAdmin):
    list_display = ('slug', 'label', 'filename_pattern', 'order')
    list_editable = ('label', 'filename_pattern', 'order')
    search_fields = ('slug', 'label', 'filename_pattern')
    actions = ['apply_patterns_to_recordings']

    @admin.action(description='Применить подстроки к существующим записям')
    def apply_patterns_to_recordings(self, request, queryset):
        applied = 0
        for tag_def in queryset:
            if not tag_def.filename_pattern:
                continue
            matched = Recording.objects.filter(filename__icontains=tag_def.filename_pattern, tag='')
            cnt = matched.update(tag=tag_def.slug)
            applied += cnt
        self.message_user(request, f'Тег применён к {applied} записям.', messages.SUCCESS)


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'event', 'username', 'ip', 'os_name', 'screen', 'recording_link')
    list_filter = ('event', 'os_name')
    search_fields = ('username', 'ip', 'user_agent')
    readonly_fields = ('username', 'ip', 'user_agent', 'os_name', 'screen', 'event', 'recording', 'created_at')
    date_hierarchy = 'created_at'

    def recording_link(self, obj):
        if obj.recording:
            return obj.recording.filename
        return '—'
    recording_link.short_description = 'Запись'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_at')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(SiteUser)
class SiteUserAdmin(admin.ModelAdmin):
    list_display = ('email', 'space', 'tg_linked', 'free_left', 'first_login_at', 'created_at')
    list_filter = ('space',)
    search_fields = ('email',)
    readonly_fields = ('created_at', 'first_login_at')
    raw_id_fields = ('space',)
    actions = ['send_telegram_message']

    def tg_linked(self, obj):
        return bool(obj.tg_chat_id)
    tg_linked.boolean = True
    tg_linked.short_description = 'TG'

    @admin.action(description='📨 Отправить Telegram-сообщение выбранным')
    def send_telegram_message(self, request, queryset):
        # Сохраняем pk выбранных пользователей в сессии и редиректим на форму
        ids = list(queryset.filter(tg_chat_id__isnull=False).values_list('pk', flat=True))
        request.session['tg_broadcast_ids'] = ids
        return redirect('../tg-broadcast/')


@admin.register(ShareToken)
class ShareTokenAdmin(admin.ModelAdmin):
    list_display = ('recording', 'token', 'is_active', 'created_at')
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    search_fields = ('recording__filename', 'token')
    readonly_fields = ('token', 'recording', 'created_at')


@admin.register(AIConfig)
class AIConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Адрес API', {
            'fields': ('url',),
            'description': 'URL LLM API, к которому отправляются запросы на генерацию саммари и вики-контента.',
        }),
        ('Заголовки запроса', {
            'fields': ('auth_header', 'referer'),
        }),
        ('Шаблон запроса', {
            'fields': ('request_template',),
            'description': (
                'JSON-шаблон тела запроса. '
                'Используйте плейсхолдеры: <code>{prompt}</code> — текст запроса, '
                '<code>{session_id}</code> — идентификатор сессии, '
                '<code>{log_id}</code> — идентификатор лога.'
            ),
        }),
        ('Разбор ответа', {
            'fields': ('response_key',),
            'description': (
                'Ключ верхнего уровня в JSON-ответе API. '
                'Если значение — список, берётся последний элемент. '
                'Если ключ не найден — используется резервный ключ <code>response</code>.'
            ),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Расширяем textarea для шаблона
        form.base_fields['request_template'].widget.attrs.update({
            'rows': 12,
            'style': 'font-family: monospace; font-size: 0.9rem;',
        })
        form.base_fields['auth_header'].widget.attrs.update({'style': 'width: 100%; font-family: monospace;'})
        return form

    def has_add_permission(self, request):
        return not AIConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Перенаправить со списка прямо на редактирование единственного объекта."""
        obj, created = AIConfig.objects.get_or_create(pk=1)
        return redirect(f'../aiconfig/{obj.pk}/change/')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('test-connection/', self.admin_site.admin_view(self._test_connection_view), name='aiconfig_test'),
        ]
        return custom + urls

    def _test_connection_view(self, request):
        """Тест соединения с LLM API."""
        from django.http import JsonResponse
        result = services.call_llm_api(
            prompt="Скажи «ОК» одним словом.",
            session_id="admin_test",
            log_id="admin_test",
            timeout=30,
        )
        if result:
            return JsonResponse({'ok': True, 'response': result[:300]})
        return JsonResponse({'ok': False, 'response': 'Нет ответа или ошибка соединения.'})

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context['test_url'] = '../test-connection/'
        return super().change_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        messages.success(request, 'Настройки LLM API сохранены.')
        return redirect(f'.')


@staff_member_required
def admin_tg_broadcast(request):
    """Форма рассылки Telegram-сообщений авторизованным пользователям."""
    import threading

    all_users_with_tg = SiteUser.objects.filter(tg_chat_id__isnull=False).select_related('space')

    # Если пришли из action — берём выбранных; иначе можно выбрать вручную
    selected_ids = request.session.pop('tg_broadcast_ids', None)

    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        mode = request.POST.get('mode', 'selected')  # all / selected
        if not text:
            messages.error(request, 'Введите текст сообщения.')
            return redirect('.')

        if mode == 'all':
            recipients = list(all_users_with_tg)
        else:
            ids = [int(x) for x in request.POST.getlist('user_ids')]
            recipients = list(SiteUser.objects.filter(pk__in=ids, tg_chat_id__isnull=False))

        def _send():
            from .telegram_service import send_message
            ok = fail = 0
            for u in recipients:
                try:
                    send_message(u.tg_chat_id, text)
                    ok += 1
                except Exception:
                    fail += 1
            import logging as _log
            _log.getLogger(__name__).info('TG broadcast: ok=%d fail=%d', ok, fail)

        threading.Thread(target=_send, daemon=True).start()
        messages.success(request, f'Рассылка запущена для {len(recipients)} пользователей.')
        return redirect('../../siteuser/')

    return render(request, 'admin/recordings/tg_broadcast.html', {
        'all_users': all_users_with_tg,
        'selected_ids': selected_ids or [],
        'title': 'Telegram-рассылка',
    })


@staff_member_required
def admin_worker_status(request):
    """Состояние воркеров: последний опрос, размеры очередей."""
    last_log = PollLog.objects.order_by('-started_at').first()
    transcribe_count = TranscribeQueue.objects.count()
    embedding_count = EmbeddingQueue.objects.count()
    return render(request, 'admin/recordings/worker_status.html', {
        'last_log': last_log,
        'transcribe_count': transcribe_count,
        'embedding_count': embedding_count,
        'title': 'Состояние воркеров',
    })


@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value_preview', 'description', 'updated_at')
    search_fields = ('key', 'description')
    list_editable = ()
    readonly_fields = ('updated_at',)

    def value_preview(self, obj):
        v = obj.value or ''
        if obj.key and 'key' in obj.key.lower() and len(v) > 8:
            return v[:8] + '…' + v[-4:]
        return (v[:60] + '…') if len(v) > 60 else v
    value_preview.short_description = 'Значение'


@admin.register(SpeakerProfile)
class SpeakerProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'space', 'created_at')
    list_filter = ('space',)
    search_fields = ('name',)
    raw_id_fields = ('space',)


@admin.register(OcrJob)
class OcrJobAdmin(admin.ModelAdmin):
    list_display = ('pk', 'original_filename', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('original_filename',)
    readonly_fields = ('created_at',)


@admin.register(MagicLoginToken)
class MagicLoginTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'token', 'expires_at', 'used_at')
    list_filter = ('used_at',)
    search_fields = ('user__email',)
    raw_id_fields = ('user',)
    readonly_fields = ('token', 'expires_at', 'used_at')

    def has_add_permission(self, request):
        return False


@admin.register(OrgRegistration)
class OrgRegistrationAdmin(admin.ModelAdmin):
    list_display = ('org_name', 'email', 'status', 'tg_chat_id', 'space', 'created_at')
    list_filter = ('status',)
    search_fields = ('org_name', 'email')
    raw_id_fields = ('space',)
    readonly_fields = ('verify_code', 'created_at')


@admin.register(MascotLog)
class MascotLogAdmin(admin.ModelAdmin):
    list_display = ('pk', 'room', 'event', 'speaker', 'text_preview', 'created_at')
    list_filter = ('event',)
    search_fields = ('room', 'text', 'speaker')
    readonly_fields = ('created_at',)

    def text_preview(self, obj):
        return (obj.text[:80] + '…') if len(obj.text) > 80 else obj.text
    text_preview.short_description = 'Текст'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MascotTask)
class MascotTaskAdmin(admin.ModelAdmin):
    list_display = ('pk', 'room', 'title_preview', 'speaker', 'done', 'created_at')
    list_filter = ('done',)
    list_editable = ('done',)
    search_fields = ('room', 'title')
    readonly_fields = ('created_at',)

    def title_preview(self, obj):
        return (obj.title[:80] + '…') if len(obj.title) > 80 else obj.title
    title_preview.short_description = 'Задача'


@admin.register(MeetingRoom)
class MeetingRoomAdmin(admin.ModelAdmin):
    list_display = ('room_name', 'title', 'space', 'with_mascot', 'created_by', 'created_at', 'ended_at')
    list_filter = ('space', 'with_mascot')
    search_fields = ('room_name', 'title')
    raw_id_fields = ('space', 'created_by')
    readonly_fields = ('created_at',)


@admin.register(CustomBot)
class CustomBotAdmin(admin.ModelAdmin):
    list_display = ('name', 'username', 'owner', 'space', 'root_article', 'is_active', 'created_at')
    list_filter = ('is_active', 'space')
    search_fields = ('name', 'username', 'owner__email')
    raw_id_fields = ('owner', 'space', 'root_article')
    readonly_fields = ('webhook_secret', 'created_at')
    list_editable = ('is_active',)


@admin.register(BotChatHistory)
class BotChatHistoryAdmin(admin.ModelAdmin):
    list_display = ('chat_id', 'bot_id', 'role', 'content_preview', 'tool_name', 'created_at')
    list_filter = ('role', 'bot_id')
    search_fields = ('chat_id', 'content', 'tool_name')
    readonly_fields = ('created_at',)
    actions = ['clear_selected_history']

    def content_preview(self, obj):
        return (obj.content[:80] + '…') if len(obj.content) > 80 else obj.content
    content_preview.short_description = 'Содержимое'

    @admin.action(description='Очистить выбранные записи истории')
    def clear_selected_history(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'Удалено {count} записей истории.')


@admin.register(BotSetupState)
class BotSetupStateAdmin(admin.ModelAdmin):
    list_display = ('chat_id', 'state', 'pending_bot_pk', 'updated_at')
    search_fields = ('chat_id',)
    readonly_fields = ('updated_at',)


# Регистрация кастомного URL в админке
try:
    from .models import RecordingEmbedding
    @admin.register(RecordingEmbedding)
    class RecordingEmbeddingAdmin(admin.ModelAdmin):
        list_display = ('recording',)
        raw_id_fields = ('recording',)
except (ImportError, AttributeError):
    pass
