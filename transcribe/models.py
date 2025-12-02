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
    user_uuid = models.CharField(max_length=36, blank=True, null=True, verbose_name="UUID пользователя")
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
    transcription_logs = models.TextField(blank=True, null=True, verbose_name="Логи транскрибации")
    original_file_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="Путь к оригинальному файлу")
    detected_language = models.CharField(max_length=10, blank=True, null=True, verbose_name="Определенный язык")
    selected_language = models.CharField(max_length=10, blank=True, null=True, verbose_name="Выбранный язык")
    language_confirmed = models.BooleanField(default=False, verbose_name="Язык подтвержден пользователем")

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


class IPUploadCount(models.Model):
    """Модель для отслеживания количества загрузок по IP адресу"""
    ip_address = models.GenericIPAddressField(unique=True, verbose_name="IP адрес")
    upload_count = models.IntegerField(default=0, verbose_name="Количество загрузок")
    balance = models.IntegerField(default=0, verbose_name="Баланс")
    is_paid = models.BooleanField(default=False, verbose_name="Оплачено")
    last_upload_at = models.DateTimeField(auto_now=True, verbose_name="Последняя загрузка")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    
    class Meta:
        verbose_name = "Счетчик загрузок по IP"
        verbose_name_plural = "Счетчики загрузок по IP"
        ordering = ['-last_upload_at']
        indexes = [
            models.Index(fields=['ip_address']),
        ]
    
    def __str__(self):
        return f"{self.ip_address} - {self.upload_count} загрузок, баланс: {self.balance}"
    
    @classmethod
    def get_or_create_for_ip(cls, ip_address):
        """Получить или создать счетчик для IP адреса"""
        obj, created = cls.objects.get_or_create(
            ip_address=ip_address,
            defaults={'upload_count': 0, 'balance': 0, 'is_paid': False}
        )
        return obj
    
    def increment_upload(self):
        """Увеличить счетчик загрузок"""
        self.upload_count += 1
        self.last_upload_at = timezone.now()
        self.save()
    
    def get_monthly_count(self):
        """Получить количество загрузок за текущий месяц"""
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return Transcription.objects.filter(
            ip_address=self.ip_address,
            uploaded_at__gte=month_start
        ).count()
    
    def requires_payment(self):
        """Проверяет, требуется ли оплата (после 2-й загрузки за месяц)"""
        monthly_count = self.get_monthly_count()
        return monthly_count >= 2 and not self.is_paid and self.balance <= 0


class UUIDUploadCount(models.Model):
    """Модель для отслеживания количества загрузок по UUID"""
    uuid = models.CharField(max_length=36, unique=True, verbose_name="UUID пользователя")
    upload_count = models.IntegerField(default=0, verbose_name="Количество загрузок")
    balance = models.IntegerField(default=0, verbose_name="Баланс")
    is_paid = models.BooleanField(default=False, verbose_name="Оплачено")
    last_upload_at = models.DateTimeField(auto_now=True, verbose_name="Последняя загрузка")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    
    class Meta:
        verbose_name = "Счетчик загрузок по UUID"
        verbose_name_plural = "Счетчики загрузок по UUID"
        ordering = ['-last_upload_at']
        indexes = [
            models.Index(fields=['uuid']),
        ]
    
    def __str__(self):
        return f"{self.uuid} - {self.upload_count} загрузок, баланс: {self.balance}"
    
    @classmethod
    def get_or_create_for_uuid(cls, uuid_str):
        """Получить или создать счетчик для UUID"""
        obj, created = cls.objects.get_or_create(
            uuid=uuid_str,
            defaults={'upload_count': 0, 'balance': 0, 'is_paid': False}
        )
        return obj
    
    def increment_upload(self):
        """Увеличить счетчик загрузок"""
        self.upload_count += 1
        self.last_upload_at = timezone.now()
        self.save()
    
    def get_monthly_count(self):
        """Получить количество загрузок за текущий месяц"""
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return Transcription.objects.filter(
            user_uuid=self.uuid,
            uploaded_at__gte=month_start
        ).count()
    
    def requires_payment(self):
        """Проверяет, требуется ли оплата (после 2-й загрузки за месяц)"""
        monthly_count = self.get_monthly_count()
        return monthly_count >= 2 and not self.is_paid and self.balance <= 0

