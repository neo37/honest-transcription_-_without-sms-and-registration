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
    list_display = ('id', 'filename', 'uploaded_at', 'ip_address', 'status', 'file_size_mb', 'has_password', 'has_screenshots', 'public_token_short', 'extract_screenshots', 'whisper_model')
    list_filter = ('status', 'uploaded_at', 'extract_screenshots', 'whisper_model')
    search_fields = ('filename', 'transcribed_text', 'ip_address', 'signature', 'public_token', 'upload_session')
    readonly_fields = ('uploaded_at', 'ip_address', 'file_size', 'transcribed_text', 'error_message', 'public_token', 'upload_session', 'password_phrase_hash', 'public_url_link', 'session_files_link')
    fieldsets = (
        ('Основная информация', {
            'fields': ('filename', 'uploaded_at', 'ip_address', 'signature', 'file_size', 'status', 'extract_screenshots', 'whisper_model')
        }),
        ('Доступ', {
            'fields': ('password_phrase_hash', 'public_token', 'public_url_link', 'upload_session', 'session_files_link')
        }),
        ('Транскрипция', {
            'fields': ('transcribed_text',)
        }),
        ('Ошибки', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
    )
    inlines = [ScreenshotInline]
    
    actions = ['generate_public_tokens', 'mark_as_completed', 'mark_as_error']
    
    def file_size_mb(self, obj):
        """Размер файла в МБ"""
        if obj.file_size:
            return f"{obj.file_size / (1024*1024):.2f} МБ"
        return "-"
    file_size_mb.short_description = "Размер"
    
    def has_password(self, obj):
        """Есть ли пароль"""
        return "✓" if obj.password_phrase_hash else "✗"
    has_password.short_description = "Пароль"
    has_password.boolean = True
    
    def has_screenshots(self, obj):
        """Есть ли скриншоты"""
        count = obj.screenshots.count()
        return f"✓ ({count})" if count > 0 else "✗"
    has_screenshots.short_description = "Скриншоты"
    
    def public_token_short(self, obj):
        """Короткий токен"""
        if obj.public_token:
            return obj.public_token[:16] + "..."
        return "-"
    public_token_short.short_description = "Токен"
    
    def public_url_link(self, obj):
        """Ссылка на публичную страницу"""
        if obj.public_token and obj.password_phrase_hash:
            import hashlib
            password_token = hashlib.sha256(f"{obj.public_token}_{obj.password_phrase_hash}".encode()).hexdigest()[:16]
            url = f"/public/{obj.public_token}/?p={password_token}"
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
    
    def mark_as_error(self, request, queryset):
        """Пометить как ошибка"""
        count = queryset.update(status='error')
        self.message_user(request, f'Помечено как ошибка: {count}')
    mark_as_error.short_description = "Пометить как ошибка"


@admin.register(Screenshot)
class ScreenshotAdmin(admin.ModelAdmin):
    list_display = ('id', 'transcription_link', 'timestamp', 'order', 'image_path', 'preview_image')
    list_filter = ('transcription', 'order')
    search_fields = ('transcription__filename', 'image_path')
    readonly_fields = ('timestamp', 'order', 'image_path', 'preview_image', 'full_image')
    
    def transcription_link(self, obj):
        """Ссылка на транскрипцию"""
        if obj.transcription:
            url = reverse('admin:transcribe_transcription_change', args=[obj.transcription.id])
            return format_html('<a href="{}">{}</a>', url, obj.transcription.filename)
        return "-"
    transcription_link.short_description = "Транскрипция"
    
    def preview_image(self, obj):
        """Превью изображения"""
        if obj and obj.image_path:
            from django.conf import settings
            url = f"{settings.MEDIA_URL}{obj.image_path}"
            return format_html('<img src="{}" style="max-width: 200px; max-height: 150px;" />', url)
        return "-"
    preview_image.short_description = "Превью"
    
    def full_image(self, obj):
        """Полное изображение"""
        if obj and obj.image_path:
            from django.conf import settings
            url = f"{settings.MEDIA_URL}{obj.image_path}"
            return format_html('<img src="{}" style="max-width: 800px;" />', url)
        return "-"
    full_image.short_description = "Полное изображение"

