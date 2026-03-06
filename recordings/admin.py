from django.contrib import admin
from django.shortcuts import render, redirect
from django.urls import path
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils.html import format_html

from .models import Recording, PollLog, Comment, TranscribeQueue, EmbeddingQueue, TagDefinition, AccessLog, ShareToken, Space, SiteUser, AIConfig
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
        for rec in queryset:
            if rec.status not in (Recording.Status.DONE, Recording.Status.TRANSCRIBING):
                enqueue_transcribe(rec, priority=1)
        count = queryset.exclude(
            status__in=(Recording.Status.DONE, Recording.Status.TRANSCRIBING),
        ).count()
        self.message_user(
            request,
            f'В очередь транскрибации добавлено записей: {count}. Воркер обработает их по очереди.',
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
    list_display = ('recording', 'priority', 'created_at')
    list_filter = ('priority',)
    search_fields = ('recording__filename',)
    ordering = ('-priority', 'created_at')
    raw_id_fields = ('recording',)


@admin.register(EmbeddingQueue)
class EmbeddingQueueAdmin(admin.ModelAdmin):
    list_display = ('recording', 'created_at')
    search_fields = ('recording__filename',)
    raw_id_fields = ('recording',)


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
    list_display = ('email', 'space', 'free_left', 'first_login_at', 'created_at')
    list_filter = ('space',)
    search_fields = ('email',)
    readonly_fields = ('created_at', 'first_login_at')
    raw_id_fields = ('space',)


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


# Регистрация кастомного URL в админке
try:
    from .models import RecordingEmbedding
    @admin.register(RecordingEmbedding)
    class RecordingEmbeddingAdmin(admin.ModelAdmin):
        list_display = ('recording',)
        raw_id_fields = ('recording',)
except (ImportError, AttributeError):
    pass
