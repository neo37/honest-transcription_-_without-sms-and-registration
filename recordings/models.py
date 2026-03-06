import uuid as _uuid

from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class Space(models.Model):
    """Пространство (организация) — группирует пользователей и записи."""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('Slug', unique=True, max_length=80)
    api_key = models.UUIDField('API ключ', default=_uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Пространство'
        verbose_name_plural = 'Пространства'

    def __str__(self):
        return self.name


class SiteUser(models.Model):
    """Пользователь сайта (не Django auth)."""
    email = models.EmailField('Email', unique=True)
    password = models.CharField('Пароль (хеш)', max_length=256)
    space = models.ForeignKey(
        Space, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Пространство', related_name='members',
    )
    free_left = models.IntegerField(
        'Осталось бесплатных транскрибаций',
        default=5,
        null=True, blank=True,
        help_text='null = безлимит (BP + участники пространства)',
    )
    first_login_at = models.DateTimeField('Первый вход', null=True, blank=True)
    tg_verify_code = models.CharField('Код TG верификации', max_length=16, null=True, blank=True)
    tg_verify_expires = models.DateTimeField('Срок действия кода TG', null=True, blank=True)
    tg_verified = models.BooleanField('TG верифицирован', default=False)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Пользователь сайта'
        verbose_name_plural = 'Пользователи сайта'

    def __str__(self):
        return self.email

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def set_password(self, raw_password):
        self.password = make_password(raw_password)


class Recording(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидание (копируется)'
        STABLE = 'stable', 'Готов к транскрибации'
        TRANSCRIBING = 'transcribing', 'Транскрибируется'
        DONE = 'done', 'Готово'
        FAILED = 'failed', 'Ошибка'

    TAG_CHOICES = [
        ('', '—'),
        ('analytics', 'Аналитика'),
        ('backend', 'Бекенд'),
        ('infra', 'Инфра'),
        ('daily', 'Дейли'),
        ('marketing', 'Маркетинг'),
        ('frontend', 'Фронт'),
    ]

    QUALITY_CHOICES = [
        ('base', 'Базовое (быстро)'),
        ('small', 'Среднее'),
        ('medium', 'Хорошее'),
        ('large-v3', 'Наилучшее (медленно)'),
    ]
    LANGUAGE_CHOICES = [
        ('ru', 'Русский'),
        ('en', 'Английский'),
        ('auto', 'Автоопределение'),
    ]

    s3_key = models.CharField('Ключ S3', max_length=512, unique=True)
    filename = models.CharField('Имя файла', max_length=255, db_index=True)
    size_bytes = models.BigIntegerField('Размер (байт)', default=0)
    last_size_check_at = models.DateTimeField('Время последней проверки размера', null=True, blank=True)
    size_stable_since = models.DateTimeField('Размер стабилен с', null=True, blank=True)
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    transcription = models.TextField('Транскрипция', blank=True)
    transcription_quality = models.CharField('Качество транскрибации', max_length=20, choices=QUALITY_CHOICES, default='base')
    transcription_language = models.CharField('Язык транскрибации', max_length=10, choices=LANGUAGE_CHOICES, default='ru')
    ai_title = models.CharField('AI Название', max_length=255, blank=True)
    ai_summary = models.TextField('AI Саммари', blank=True)
    transcribed_at = models.DateTimeField('Транскбировано', null=True, blank=True)
    error_message = models.TextField('Сообщение об ошибке', blank=True)
    tag = models.CharField('Тег', max_length=20, blank=True, choices=TAG_CHOICES, db_index=True)
    space = models.ForeignKey(
        Space, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Пространство', related_name='recordings',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Запись'
        verbose_name_plural = 'Записи'

    def __str__(self):
        return self.filename


class PollLog(models.Model):
    started_at = models.DateTimeField('Начало', auto_now_add=True)
    finished_at = models.DateTimeField('Конец', null=True, blank=True)
    files_found = models.PositiveIntegerField('Найдено файлов', default=0)
    files_stable = models.PositiveIntegerField('Стабильных', default=0)
    files_transcribed = models.PositiveIntegerField('Транскрибировано', default=0)
    message = models.TextField('Сообщение', blank=True)
    success = models.BooleanField('Успех', default=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Лог опроса'
        verbose_name_plural = 'Логи опросов'


class Comment(models.Model):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name='comments')
    text = models.TextField('Текст')
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Комментарий'
        verbose_name_plural = 'Комментарии'


class TranscribeQueue(models.Model):
    """Очередь транскрибации: 0 = с поллера, 1 = по кнопке пользователя (в приоритет)."""
    recording = models.OneToOneField(
        Recording,
        on_delete=models.CASCADE,
        related_name='transcribe_queue_entry',
    )
    priority = models.PositiveSmallIntegerField(
        'Приоритет',
        default=0,
        help_text='1 = по кнопке (вне очереди), 0 = с поллера',
    )
    created_at = models.DateTimeField('Добавлено', auto_now_add=True)

    class Meta:
        ordering = ['-priority', 'created_at']
        verbose_name = 'Задача транскрибации'
        verbose_name_plural = 'Очередь транскрибации'


class EmbeddingQueue(models.Model):
    """Очередь индексации эмбеддингов для расширенного поиска."""
    recording = models.OneToOneField(
        Recording,
        on_delete=models.CASCADE,
        related_name='embedding_queue_entry',
    )
    created_at = models.DateTimeField('Добавлено', auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Задача эмбеддинга'
        verbose_name_plural = 'Очередь эмбеддингов'


class OcrJob(models.Model):
    """Задача OCR через внешний API (PDF/изображения → Markdown)."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'В очереди'
        PROCESSING = 'processing', 'Обрабатывается'
        DONE = 'done', 'Готово'
        FAILED = 'failed', 'Ошибка'

    original_filename = models.CharField('Имя файла', max_length=255)
    file_path = models.CharField('Путь к файлу', max_length=512, blank=True)
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    result_markdown = models.TextField('Результат (Markdown)', blank=True)
    error_message = models.TextField('Ошибка', blank=True)
    space = models.ForeignKey(
        'Space', null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Пространство', related_name='ocr_jobs',
    )
    share_token = models.UUIDField('Токен публичного доступа', null=True, blank=True, unique=True)
    is_public = models.BooleanField('Публичный доступ', default=False)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Задача OCR'
        verbose_name_plural = 'Задачи OCR'


class TagDefinition(models.Model):
    """Тег/фильтр, управляемый из админки. Может иметь подстроку для авто-назначения."""
    slug = models.SlugField('Ключ (slug)', max_length=50, unique=True)
    label = models.CharField('Название', max_length=100)
    filename_pattern = models.CharField(
        'Подстрока в имени файла',
        max_length=200,
        blank=True,
        help_text='Если задана, записи с этой подстрокой в имени будут авто-тегироваться (без учёта регистра)',
    )
    order = models.PositiveIntegerField('Порядок отображения', default=0)

    class Meta:
        ordering = ['order', 'label']
        verbose_name = 'Тег (фильтр)'
        verbose_name_plural = 'Теги (фильтры)'

    def __str__(self):
        return self.label


class AccessLog(models.Model):
    """Лог входов и просмотров записей."""
    EVENT_LOGIN = 'login'
    EVENT_VIEW = 'view'
    EVENT_CHOICES = [
        ('login', 'Вход'),
        ('view', 'Просмотр записи'),
    ]
    username = models.CharField('Пользователь', max_length=150, blank=True)
    ip = models.GenericIPAddressField('IP адрес', null=True, blank=True)
    user_agent = models.TextField('User-Agent', blank=True)
    os_name = models.CharField('ОС', max_length=200, blank=True)
    screen = models.CharField('Разрешение экрана', max_length=30, blank=True)
    event = models.CharField('Событие', max_length=20, choices=EVENT_CHOICES, db_index=True)
    recording = models.ForeignKey(
        Recording, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='access_logs',
    )
    created_at = models.DateTimeField('Время', auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Лог доступа'
        verbose_name_plural = 'Логи доступа'

    def __str__(self):
        return f'{self.event} | {self.username} | {self.ip}'


class ShareToken(models.Model):
    """Публичная ссылка на запись (без авторизации)."""
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name='share_tokens')
    token = models.UUIDField('Токен', default=_uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Публичная ссылка'
        verbose_name_plural = 'Публичные ссылки'

    def __str__(self):
        return f'{self.recording.filename} — {self.token}'


class MagicLoginToken(models.Model):
    """Одноразовая ссылка для автоматической авторизации пользователя."""
    user = models.ForeignKey(
        SiteUser, on_delete=models.CASCADE,
        related_name='magic_tokens', verbose_name='Пользователь',
    )
    token = models.UUIDField('Токен', default=_uuid.uuid4, unique=True, editable=False)
    expires_at = models.DateTimeField('Действует до')
    used_at = models.DateTimeField('Использован', null=True, blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Magic-ссылка'
        verbose_name_plural = 'Magic-ссылки'

    def __str__(self):
        return f'{self.user.email} — {self.token}'

    @property
    def is_valid(self):
        from django.utils import timezone
        return self.used_at is None and timezone.now() <= self.expires_at


class OrgRegistration(models.Model):
    """Заявка на регистрацию сторонней организации — подтверждается через Telegram."""
    STATUS_PENDING = 'pending'
    STATUS_VERIFIED = 'verified'
    STATUS_CHOICES = [
        ('pending', 'Ожидает подтверждения'),
        ('verified', 'Подтверждено'),
    ]

    org_name = models.CharField('Название организации', max_length=200)
    email = models.EmailField('Email администратора')
    verify_code = models.CharField('Код подтверждения', max_length=16, unique=True)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='pending')
    tg_chat_id = models.BigIntegerField('Telegram chat ID', null=True, blank=True)
    space = models.ForeignKey(
        Space, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Пространство', related_name='org_registrations',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Регистрация организации'
        verbose_name_plural = 'Регистрации организаций'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.org_name} ({self.email}) — {self.status}'


_DEFAULT_LLM_TEMPLATE = (
    '{\n'
    '  "question_to_send": "{prompt}",\n'
    '  "session_id": "{session_id}",\n'
    '  "user": "openai",\n'
    '  "log_id": "{log_id}"\n'
    '}'
)


class AIConfig(models.Model):
    """Настройки LLM API — синглтон (всегда pk=1)."""
    url = models.URLField(
        'URL API',
        default='https://r-ai.business-pad.com/api/ai_request/',
        help_text='Адрес LLM API, например https://r-ai.business-pad.com/api/ai_request/',
    )
    auth_header = models.CharField(
        'Authorization',
        max_length=512,
        default='Basic YXBpX3VzZXI6QXBpVXNlclRlc3QxMjMh',
        blank=True,
        help_text='Значение заголовка Authorization (Basic / Bearer …)',
    )
    referer = models.CharField(
        'Referer',
        max_length=256,
        default='https://core.business-pad.com/',
        blank=True,
        help_text='Значение заголовка Referer',
    )
    request_template = models.TextField(
        'Шаблон тела запроса (JSON)',
        default=_DEFAULT_LLM_TEMPLATE,
        help_text=(
            'JSON-шаблон запроса к API. '
            'Доступные плейсхолдеры: {prompt} — текст запроса, '
            '{session_id} — идентификатор сессии, {log_id} — идентификатор лога.'
        ),
    )
    response_key = models.CharField(
        'Ключ ответа в JSON',
        max_length=128,
        default='messages',
        blank=True,
        help_text=(
            'Ключ верхнего уровня в ответе API. '
            'Если значение — список, берётся последний элемент. '
            'Запасной ключ: "response".'
        ),
    )

    class Meta:
        verbose_name = 'Настройки LLM API'
        verbose_name_plural = 'Настройки LLM API'

    def __str__(self):
        return f'LLM API ({self.url})'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        try:
            return cls.objects.get(pk=1)
        except cls.DoesNotExist:
            return cls()  # экземпляр с дефолтами без сохранения в БД


# Эмбеддинг для семантического поиска (PostgreSQL + pgvector)
try:
    from pgvector.django import VectorField

    class RecordingEmbedding(models.Model):
        recording = models.OneToOneField(
            Recording,
            on_delete=models.CASCADE,
            primary_key=True,
            related_name='embedding',
        )
        # paraphrase-multilingual-MiniLM-L12-v2 → 384
        embedding = VectorField(dimensions=384, null=True, blank=True)

        class Meta:
            verbose_name = 'Эмбеддинг записи'
            verbose_name_plural = 'Эмбеддинги записей'
except ImportError:
    pass
