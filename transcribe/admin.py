from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import Transcription, Screenshot


class ScreenshotInline(admin.TabularInline):
    """Инлайн для скриншотов в транскрипции"""
    model = Screenshot
    extra = 0
    readonly_fields = ('timestamp', 'order', 'image_path', 'preview_image')
    fields = ('order', 'timestamp', 'image_path', 'preview_image')
    can_delete = True
    
    def preview_image(self, obj):
        if obj and obj.image_path:
            from django.conf import settings
            url = f"{settings.MEDIA_URL}{obj.image_path}"
            return format_html('<img src="{}" style="max-width: 200px; max-height: 150px;" />', url)
        return "-"
    preview_image.short_description = "Превью"


@admin.register(Transcription)
class TranscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'filename', 'uploaded_at', 'ip_address', 'status', 
        'file_size_mb', 'has_password', 'has_screenshots', 
        'public_token_short', 'extract_screenshots', 'whisper_model',
        'signature', 'upload_session'
    )
    list_filter = ('status', 'uploaded_at', 'extract_screenshots', 'whisper_model', 'has_password')
    search_fields = ('filename', 'transcribed_text', 'ip_address', 'signature', 'public_token', 'upload_session')
    readonly_fields = (
        'uploaded_at', 'ip_address', 'file_size', 'transcribed_text', 
        'error_message', 'public_token', 'upload_session', 
        'password_phrase_hash', 'public_url_link', 'session_files_link',
        'screenshots_count', 'transcribed_text_preview'
    )
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'filename', 'uploaded_at', 'ip_address', 'signature', 
                'file_size', 'status', 'extract_screenshots', 'whisper_model'
            )
        }),
        ('Доступ', {
            'fields': (
                'password_phrase_hash', 'public_token', 'public_url_link', 
                'upload_session', 'session_files_link'
            )
        }),
        ('Транскрипция', {
            'fields': ('transcribed_text_preview', 'transcribed_text', 'screenshots_count')
        }),
        ('Ошибки', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
    )
    inlines = [ScreenshotInline]
    
    actions = ['generate_public_tokens', 'mark_as_completed', 'mark_as_error', 'mark_as_processing']
    
    def file_size_mb(self, obj):
        """Размер файла в МБ"""
        if obj.file_size:
            return f"{obj.file_size / (1024*1024):.2f} МБ"
        return "-"
    file_size_mb.short_description = "Размер"
    file_size_mb.admin_order_field = 'file_size'
    
    def has_password(self, obj):
        """Есть ли пароль - возвращает boolean для корректной работы в админке"""
        return bool(obj.password_phrase_hash)
    has_password.short_description = "Пароль"
    has_password.boolean = True
    has_password.admin_order_field = 'password_phrase_hash'
    
    def has_screenshots(self, obj):
        """Есть ли скриншоты - возвращает boolean"""
        count = obj.screenshots.count()
        return count > 0
    has_screenshots.short_description = "Скриншоты"
    has_screenshots.boolean = True
    
    def screenshots_count(self, obj):
        """Количество скриншотов"""
        count = obj.screenshots.count()
        if count > 0:
            return format_html('<strong>{}</strong> скриншотов', count)
        return "Нет скриншотов"
    screenshots_count.short_description = "Количество скриншотов"
    
    def public_token_short(self, obj):
        """Короткий токен"""
        if obj.public_token:
            return obj.public_token[:16] + "..."
        return "-"
    public_token_short.short_description = "Токен"
    
    def transcribed_text_preview(self, obj):
        """Превью транскрипции"""
        if obj.transcribed_text:
            preview = obj.transcribed_text[:200]
            if len(obj.transcribed_text) > 200:
                preview += "..."
            return format_html('<div style="max-height: 100px; overflow-y: auto; padding: 5px; background: #f5f5f5; border-radius: 3px;">{}</div>', preview)
        return "-"
    transcribed_text_preview.short_description = "Превью текста"
    
    def public_url_link(self, obj):
        """Ссылка на публичную страницу"""
        if obj.public_token:
            url = f"/public/{obj.public_token}/"
            if obj.password_phrase_hash:
                import hashlib
                password_token = hashlib.sha256(f"{obj.public_token}_{obj.password_phrase_hash}".encode()).hexdigest()[:16]
                url += f"?p={password_token}"
            return format_html('<a href="{}" target="_blank">Открыть публичную ссылку</a>', url)
        return "-"
    public_url_link.short_description = "Публичная ссылка"
    
    def session_files_link(self, obj):
        """Ссылки на файлы из той же сессии"""
        if obj.upload_session:
            from .models import Transcription
            related = Transcription.objects.filter(upload_session=obj.upload_session).exclude(id=obj.id)
            if related.exists():
                links = []
                for t in related:
                    url = reverse('admin:transcribe_transcription_change', args=[t.id])
                    links.append(f'<a href="{url}">{t.filename}</a>')
                return format_html('<br>'.join(links))
        return "-"
    session_files_link.short_description = "Файлы сессии"
    
    def generate_public_tokens(self, request, queryset):
        """Генерирует публичные токены для выбранных транскрипций"""
        count = 0
        for transcription in queryset:
            if not transcription.public_token:
                transcription.generate_public_token()
                count += 1
        self.message_user(request, f'Сгенерировано токенов: {count}')
    generate_public_tokens.short_description = "Сгенерировать публичные токены"
    
    def mark_as_completed(self, request, queryset):
        """Пометить как завершенные"""
        count = queryset.update(status='completed')
        self.message_user(request, f'Помечено как завершенные: {count}')
    mark_as_completed.short_description = "Пометить как завершенные"
    
    def mark_as_processing(self, request, queryset):
        """Пометить как обрабатывается"""
        count = queryset.update(status='processing')
        self.message_user(request, f'Помечено как обрабатывается: {count}')
    mark_as_processing.short_description = "Пометить как обрабатывается"
    
    def mark_as_error(self, request, queryset):
        """Пометить как ошибка"""
        count = queryset.update(status='error')
        self.message_user(request, f'Помечено как ошибка: {count}')
    mark_as_error.short_description = "Пометить как ошибка"


@admin.register(Screenshot)
class ScreenshotAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'transcription_link', 'timestamp', 'order', 
        'image_path', 'preview_image'
    )
    list_filter = ('transcription', 'order', 'timestamp')
    search_fields = ('transcription__filename', 'image_path', 'transcription__upload_session')
    readonly_fields = ('timestamp', 'order', 'image_path', 'preview_image', 'full_image', 'transcription_link')
    fieldsets = (
        ('Основная информация', {
            'fields': ('transcription_link', 'timestamp', 'order', 'image_path')
        }),
        ('Изображение', {
            'fields': ('preview_image', 'full_image')
        }),
    )
    
    def transcription_link(self, obj):
        """Ссылка на транскрипцию"""
        if obj.transcription:
            url = reverse('admin:transcribe_transcription_change', args=[obj.transcription.id])
            return format_html('<a href="{}">{}</a>', url, obj.transcription.filename)
        return "-"
    transcription_link.short_description = "Транскрипция"
    transcription_link.admin_order_field = 'transcription__filename'
    
    def preview_image(self, obj):
        """Превью изображения"""
        if obj and obj.image_path:
            from django.conf import settings
            url = f"{settings.MEDIA_URL}{obj.image_path}"
            return format_html('<img src="{}" style="max-width: 200px; max-height: 150px; border: 1px solid #ddd; border-radius: 3px;" />', url)
        return "-"
    preview_image.short_description = "Превью"
    
    def full_image(self, obj):
        """Полное изображение"""
        if obj and obj.image_path:
            from django.conf import settings
            url = f"{settings.MEDIA_URL}{obj.image_path}"
            return format_html('<img src="{}" style="max-width: 800px; border: 1px solid #ddd; border-radius: 3px;" />', url)
        return "-"
    full_image.short_description = "Полное изображение"
