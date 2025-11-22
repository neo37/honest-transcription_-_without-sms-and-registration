from django.db import models
from django.utils import timezone
import hashlib
import secrets


class Transcription(models.Model):
    """Модель для хранения транскрипций"""
    WHISPER_MODELS = [
        ('tiny', 'Tiny (быстрая, низкое качество)'),
        ('base', 'Base (баланс, по умолчанию)'),
        ('small', 'Small (хорошее качество)'),
        ('medium', 'Medium (высокое качество)'),
        ('large-v2', 'Large-v2 (лучшее качество, медленно)'),
        ('large-v3', 'Large-v3 (лучшее качество, медленно)'),
    ]
    
    filename = models.CharField(max_length=255, verbose_name="Имя файла")
    uploaded_at = models.DateTimeField(default=timezone.now, verbose_name="Дата загрузки")
    ip_address = models.GenericIPAddressField(verbose_name="IP адрес")
    signature = models.CharField(max_length=500, blank=True, null=True, verbose_name="Подпись")
    password_phrase_hash = models.CharField(max_length=64, blank=True, null=True, verbose_name="Хеш фразы-пароля")
    public_token = models.CharField(max_length=32, unique=True, blank=True, null=True, verbose_name="Публичный токен для доступа")
    transcribed_text = models.TextField(verbose_name="Транскрибированный текст")
    file_size = models.BigIntegerField(verbose_name="Размер файла (байты)")
    extract_screenshots = models.BooleanField(default=False, verbose_name="Извлечь скриншоты")
    upload_session = models.CharField(max_length=100, blank=True, null=True, verbose_name="Сессия загрузки")
    whisper_model = models.CharField(max_length=20, choices=WHISPER_MODELS, default='base', verbose_name="Модель Whisper")
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Ожидает обработки'),
            ('processing', 'Обрабатывается'),
            ('completed', 'Завершено'),
            ('error', 'Ошибка'),
        ],
        default='pending',
        verbose_name="Статус"
    )
    error_message = models.TextField(blank=True, null=True, verbose_name="Сообщение об ошибке")

    class Meta:
        verbose_name = "Транскрипция"
        verbose_name_plural = "Транскрипции"
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['password_phrase_hash']),
        ]

    def __str__(self):
        return f"{self.filename} - {self.uploaded_at.strftime('%Y-%m-%d %H:%M')}"
    
    @staticmethod
    def hash_password_phrase(phrase):
        """Хеширует фразу-пароль"""
        if not phrase:
            return None
        return hashlib.sha256(phrase.encode('utf-8')).hexdigest()
    
    def check_password_phrase(self, phrase):
        """Проверяет фразу-пароль"""
        if not self.password_phrase_hash:
            return True  # Если пароль не установлен, доступ открыт
        if not phrase:
            return False
        return self.password_phrase_hash == self.hash_password_phrase(phrase)
    
    def generate_public_token(self):
        """Генерирует публичный токен для доступа"""
        if not self.public_token:
            self.public_token = secrets.token_urlsafe(24)[:32]
            self.save()
        return self.public_token


class Screenshot(models.Model):
    """Модель для хранения скриншотов из видео"""
    transcription = models.ForeignKey(Transcription, on_delete=models.CASCADE, related_name='screenshots', verbose_name="Транскрипция")
    timestamp = models.FloatField(verbose_name="Временная метка (секунды)")
    image_path = models.CharField(max_length=500, verbose_name="Путь к изображению")
    order = models.IntegerField(default=0, verbose_name="Порядок")
    
    class Meta:
        verbose_name = "Скриншот"
        verbose_name_plural = "Скриншоты"
        ordering = ['order', 'timestamp']
    
    def __str__(self):
        return f"{self.transcription.filename} - {self.timestamp:.0f}s"

