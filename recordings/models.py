import uuid as _uuid

from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class SystemConfig(models.Model):
    """Системные настройки платформы (key/value)."""
    key = models.CharField('Ключ', max_length=100, unique=True)
    value = models.CharField('Значение', max_length=500, blank=True, default='')
    description = models.CharField('Описание', max_length=300, blank=True, default='')
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Системная настройка'
        verbose_name_plural = 'Системные настройки'

    def __str__(self):
        return f'{self.key} = {self.value}'

    @classmethod
    def get(cls, key, default=''):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value, description=''):
        obj, _ = cls.objects.get_or_create(key=key, defaults={'description': description})
        obj.value = str(value)
        obj.save(update_fields=['value', 'updated_at'])
        return obj


class Space(models.Model):
    """Пространство (организация) — группирует пользователей и записи."""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('Slug', unique=True, max_length=80)
    api_key = models.UUIDField('API ключ', default=_uuid.uuid4, editable=False, unique=True)
    wiki_username = models.CharField('Wiki API логин', max_length=100, blank=True, default='')
    wiki_password = models.CharField('Wiki API пароль', max_length=100, blank=True, default='')
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
    tg_chat_id = models.BigIntegerField('Telegram Chat ID', null=True, blank=True)
    tg_username = models.CharField('Telegram username', max_length=100, blank=True, default='')
    google_calendar_token = models.JSONField('Google Calendar токен', null=True, blank=True)
    display_name = models.CharField('Отображаемое имя', max_length=100, blank=True, default='')
    avatar_url = models.CharField('Аватар URL', max_length=500, blank=True, default='')
    timezone = models.CharField('Часовой пояс', max_length=60, default='Europe/Moscow')
    last_seen = models.DateTimeField('Последняя активность', null=True, blank=True)
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
    speaker_names = models.JSONField('Имена спикеров', default=dict, blank=True)
    speaker_embeddings = models.JSONField('Эмбеддинги спикеров', default=dict, blank=True)
    transcription_progress = models.PositiveSmallIntegerField('Прогресс транскрибации (%)', default=0)
    transcription_stage = models.CharField('Этап транскрибации', max_length=100, blank=True)
    transcription_log = models.JSONField('Лог транскрибации', default=list, blank=True)
    ai_title = models.CharField('AI Название', max_length=255, blank=True)
    ai_summary = models.TextField('AI Саммари', blank=True)
    transcribed_at = models.DateTimeField('Транскбировано', null=True, blank=True)
    error_message = models.TextField('Сообщение об ошибке', blank=True)
    tag = models.CharField('Тег', max_length=20, blank=True, choices=TAG_CHOICES, db_index=True)
    space = models.ForeignKey(
        Space, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Пространство', related_name='recordings',
    )
    owner = models.ForeignKey(
        'SiteUser', null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Владелец', related_name='owned_recordings',
    )
    is_personal = models.BooleanField('Личная запись', default=False, db_index=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Запись'
        verbose_name_plural = 'Записи'

    @property
    def speaker_names_json(self):
        import json
        return json.dumps(self.speaker_names or {}, ensure_ascii=False)

    def __str__(self):
        return self.filename


class SpeakerProfile(models.Model):
    """Голосовой профиль спикера — хранит усреднённый эмбеддинг для автоопределения."""
    space = models.ForeignKey(
        Space, on_delete=models.CASCADE,
        verbose_name='Пространство', related_name='speaker_profiles',
    )
    name = models.CharField('Имя', max_length=100)
    embedding = models.JSONField('Эмбеддинг голоса', default=list, blank=True)
    speech_patterns = models.JSONField('Речевые паттерны', default=dict, blank=True,
        help_text='{"top_words": {"слово": freq, ...}, "phrases": {"фраза": freq, ...}, "sample_words": N}')
    sample_count = models.PositiveIntegerField('Кол-во образцов', default=1)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Голосовой профиль'
        verbose_name_plural = 'Голосовые профили'
        unique_together = [('space', 'name')]

    def __str__(self):
        return f'{self.name} ({self.space})'


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
    EVENT_DOWNLOAD = 'download'
    EVENT_CHOICES = [
        ('login', 'Вход'),
        ('view', 'Просмотр записи'),
        ('download', 'Скачивание записи'),
    ]
    username = models.CharField('Пользователь', max_length=150, blank=True)
    ip = models.GenericIPAddressField('IP адрес', null=True, blank=True)
    user_agent = models.TextField('User-Agent', blank=True)
    os_name = models.CharField('ОС', max_length=200, blank=True)
    screen = models.CharField('Разрешение экрана', max_length=30, blank=True)
    tg_chat_id = models.BigIntegerField('Telegram Chat ID', null=True, blank=True)
    tg_username = models.CharField('Telegram username', max_length=100, blank=True, default='')
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
    target_space = models.ForeignKey(
        Space, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Целевое пространство (если задано — не создаётся новое)',
        related_name='target_registrations',
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


class MascotLog(models.Model):
    """Лог действий голосового агента Маскот."""
    EVENT_JOINED = 'joined'
    EVENT_HEARD = 'heard'
    EVENT_SAID = 'said'
    EVENT_WAKE = 'wake'
    EVENT_EMOJI = 'emoji'
    EVENT_CHAT = 'chat'
    EVENT_CHOICES = [
        (EVENT_JOINED, 'Вошёл в комнату'),
        (EVENT_HEARD,  'Услышал'),
        (EVENT_SAID,   'Сказал'),
        (EVENT_WAKE,   'Wake-word'),
        (EVENT_EMOJI,  'Emoji реакция'),
        (EVENT_CHAT,   'Сообщение в чате'),
    ]
    room = models.CharField('Комната', max_length=200)
    event = models.CharField('Событие', max_length=20, choices=EVENT_CHOICES)
    text = models.TextField('Текст', blank=True)
    speaker = models.CharField('Участник', max_length=200, blank=True)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        verbose_name = 'Лог Маскота'
        verbose_name_plural = 'Логи Маскота'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.room}] {self.get_event_display()}: {self.text[:60]}'


class MascotTask(models.Model):
    """Задача, созданная голосовым агентом Маскот."""
    room = models.CharField('Комната', max_length=200)
    title = models.CharField('Задача', max_length=500)
    speaker = models.CharField('Автор', max_length=200, blank=True)
    done = models.BooleanField('Выполнена', default=False)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        verbose_name = 'Задача Маскота'
        verbose_name_plural = 'Задачи Маскота'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.room}] {self.title[:80]}'


class MeetingRoom(models.Model):
    """Встреча с LiveKit — опциональный голосовой маскот, приглашения через Telegram."""
    REPEAT_NONE = ''
    REPEAT_WEEKLY = 'weekly'
    REPEAT_CHOICES = [
        ('', 'Не повторяется'),
        ('weekly', 'Еженедельно'),
    ]

    room_name = models.CharField('ID комнаты', max_length=100, unique=True)
    title = models.CharField('Название встречи', max_length=200)
    with_mascot = models.BooleanField('С маскотом', default=False)
    created_by = models.ForeignKey(
        SiteUser, on_delete=models.SET_NULL, null=True, related_name='meetings'
    )
    space = models.ForeignKey(
        Space, on_delete=models.SET_NULL, null=True, blank=True, related_name='meetings'
    )
    ended_at = models.DateTimeField('Завершена', null=True, blank=True)
    scheduled_at = models.DateTimeField('Запланировано', null=True, blank=True)
    repeat = models.CharField('Повтор', max_length=20, choices=REPEAT_CHOICES, default='', blank=True)
    join_url = models.URLField('Ссылка на встречу', max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    google_event_id = models.CharField('Google Event ID', max_length=200, null=True, blank=True, db_index=True)
    guest_tg_chat_id = models.BigIntegerField('Telegram гостя', null=True, blank=True)

    class Meta:
        verbose_name = 'Встреча'
        verbose_name_plural = 'Встречи'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.room_name})'


class CustomBot(models.Model):
    """Пользовательский Telegram-бот, привязанный к разделу вики."""
    owner = models.ForeignKey(
        SiteUser, on_delete=models.CASCADE, related_name='custom_bots',
        verbose_name='Владелец',
    )
    space = models.ForeignKey(
        Space, on_delete=models.CASCADE, related_name='custom_bots',
        verbose_name='Пространство',
    )
    token = models.CharField('Токен бота', max_length=200, unique=True)
    name = models.CharField('Название бота', max_length=200, blank=True, default='')
    username = models.CharField('Username бота', max_length=100, blank=True, default='')
    # root_article — статья/раздел вики, по которому работает бот (null = весь wiki)
    root_article = models.ForeignKey(
        'wiki_kb.WikiArticle', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='custom_bots',
        verbose_name='Раздел вики',
    )
    webhook_secret = models.CharField('Webhook secret', max_length=64, blank=True, default='')
    system_prompt = models.TextField('Системный промт', blank=True, default='',
                                     help_text='Если заполнено — заменяет стандартный промт бота')

    REPLY_AUTO = 'auto'
    REPLY_AFTER_DELAY = 'after_delay'
    REPLY_TRIGGER = 'trigger'
    REPLY_OFF = 'off'
    REPLY_MODE_CHOICES = [
        (REPLY_AUTO, 'Авто — отвечать на все сообщения'),
        (REPLY_AFTER_DELAY, 'После паузы — если владелец молчит N минут'),
        (REPLY_TRIGGER, 'По слову — только если есть ключевое слово'),
        (REPLY_OFF, 'Выключен — не отвечать'),
    ]
    reply_mode = models.CharField(
        'Режим ответа', max_length=20, choices=REPLY_MODE_CHOICES, default=REPLY_AUTO,
    )
    reply_delay_m = models.PositiveIntegerField(
        'Пауза (мин)', default=5,
        help_text='Для режима «после паузы»: бот отвечает если владелец молчал N минут',
    )
    trigger_word = models.CharField(
        'Ключевое слово', max_length=100, blank=True, default='',
        help_text='Для режима «по слову»: бот отвечает только если сообщение содержит это слово',
    )

    is_active = models.BooleanField('Активен', default=True)
    public_chat_token = models.UUIDField(
        'Токен публичного чата', default=_uuid.uuid4, unique=True, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Кастомный бот'
        verbose_name_plural = 'Кастомные боты'

    def __str__(self):
        return self.username or self.name or f'Bot #{self.pk}'

    def get_article_ids(self):
        """IDs всех статей в поддереве root_article (или весь wiki space)."""
        if not self.root_article:
            return None  # весь wiki
        from wiki_kb.models import WikiArticle
        ids = [self.root_article.pk] + self.root_article.get_all_descendants_ids()
        return ids


class BotChatHistory(models.Model):
    """История диалога пользователя с ботом (для памяти агента)."""
    chat_id = models.BigIntegerField('Telegram chat_id')
    bot_id = models.IntegerField('CustomBot pk (null=главный)', null=True, blank=True)
    role = models.CharField('Роль', max_length=16)   # user / assistant / tool / audio
    content = models.TextField('Содержимое')
    tool_name = models.CharField('Инструмент', max_length=64, blank=True, default='')
    recording = models.ForeignKey(
        'Recording', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='bot_history_entries',
        verbose_name='Запись (аудио/видео)',
    )
    ocr_job = models.ForeignKey(
        'OcrJob', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='bot_history_entries',
        verbose_name='OCR задача',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'История чата бота'
        verbose_name_plural = 'История чатов ботов'
        ordering = ['created_at']
        indexes = [models.Index(fields=['chat_id', 'bot_id', 'created_at'], name='bot_chat_history_idx')]

    @classmethod
    def get_history(cls, chat_id, bot_id, last_n=20):
        return list(
            cls.objects.filter(chat_id=chat_id, bot_id=bot_id).order_by('-created_at')[:last_n]
        )[::-1]

    @classmethod
    def add(cls, chat_id, bot_id, role, content, tool_name='', recording=None, ocr_job=None):
        cls.objects.create(
            chat_id=chat_id, bot_id=bot_id, role=role,
            content=content, tool_name=tool_name, recording=recording, ocr_job=ocr_job,
        )

    @classmethod
    def clear(cls, chat_id, bot_id):
        cls.objects.filter(chat_id=chat_id, bot_id=bot_id).delete()


class BotContact(models.Model):
    """Контакт (клиент), с которым работал кастомный бот через Business Mode."""
    bot = models.ForeignKey(
        'CustomBot', on_delete=models.CASCADE, related_name='contacts',
        verbose_name='Бот',
    )
    tg_user_id = models.BigIntegerField('Telegram user_id')
    first_name = models.CharField('Имя', max_length=200, blank=True, default='')
    last_name = models.CharField('Фамилия', max_length=200, blank=True, default='')
    username = models.CharField('Username', max_length=100, blank=True, default='')
    first_seen = models.DateTimeField('Первый контакт', auto_now_add=True)
    last_seen = models.DateTimeField('Последний контакт', auto_now=True)
    note = models.TextField('Заметка', blank=True, default='',
                            help_text='Личная заметка владельца об этом контакте')

    class Meta:
        verbose_name = 'Контакт бота'
        verbose_name_plural = 'Контакты ботов'
        unique_together = [('bot', 'tg_user_id')]
        ordering = ['-last_seen']

    def __str__(self):
        name = ' '.join(filter(None, [self.first_name, self.last_name])) or f'user_{self.tg_user_id}'
        return f'{name} ({self.bot})'

    @property
    def display_name(self):
        name = ' '.join(filter(None, [self.first_name, self.last_name]))
        if self.username:
            return f'{name} (@{self.username})' if name else f'@{self.username}'
        return name or str(self.tg_user_id)


class MeetingAttendee(models.Model):
    """Участие пользователя во встрече + настройки уведомлений."""
    user = models.ForeignKey(SiteUser, on_delete=models.CASCADE, related_name='meeting_attendances')
    meeting = models.ForeignKey(MeetingRoom, on_delete=models.CASCADE, related_name='attendees')
    notify_before_minutes = models.IntegerField('Уведомить за (мин)', default=15)
    repeat_every_minutes = models.IntegerField('Повторять каждые (мин)', default=1)
    confirmed_at = models.DateTimeField('Подтверждено', null=True, blank=True)
    last_notified_at = models.DateTimeField('Последнее уведомление', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Участник встречи'
        verbose_name_plural = 'Участники встреч'
        unique_together = [('user', 'meeting')]

    def __str__(self):
        return f'{self.user.email} @ {self.meeting}'


class DayShareLink(models.Model):
    """Ссылка на доступные слоты дня — владелец отмечает занятые встречи, гость видит свободное время."""
    owner = models.ForeignKey(
        SiteUser, on_delete=models.CASCADE, related_name='day_shares',
        verbose_name='Владелец',
    )
    date = models.DateField('Дата', null=True, blank=True)
    is_permanent = models.BooleanField('Постоянная (всегда завтра)', default=False)
    share_token = models.UUIDField('Токен', default=_uuid.uuid4, unique=True, editable=False)
    # Занятые слоты: [{start: "HH:MM", end: "HH:MM", title: str}]
    busy_slots = models.JSONField('Занятые слоты', default=list)
    slot_duration_minutes = models.IntegerField('Длительность слота (мин)', default=30)
    day_start = models.CharField('Начало дня', max_length=5, default='09:00')
    day_end = models.CharField('Конец дня', max_length=5, default='18:00')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ссылка-поделиться днём'
        verbose_name_plural = 'Ссылки-поделиться днём'

    def __str__(self):
        return f'{self.owner.email} / {self.date}'


class BookingAttempt(models.Model):
    """Лог попыток бронирования для защиты от злоупотреблений."""
    ip = models.GenericIPAddressField('IP')
    fingerprint = models.CharField('Fingerprint', max_length=64, blank=True)  # md5(ua+resolution+os)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Попытка бронирования'


class BotSetupState(models.Model):
    """Временное состояние multi-step setup wizard в главном боте."""
    STATE_IDLE = ''
    STATE_AWAIT_TOKEN = 'await_token'
    STATE_PICK_ARTICLE = 'pick_article'

    chat_id = models.BigIntegerField('Telegram chat_id', unique=True)
    state = models.CharField('Состояние', max_length=50, blank=True, default='')
    # временный токен при настройке нового бота
    pending_token = models.CharField('Pending token', max_length=200, blank=True, default='')
    pending_bot_pk = models.IntegerField('Pending bot pk', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Состояние setup-wizard'
        verbose_name_plural = 'Состояния setup-wizard'

    @classmethod
    def get_state(cls, chat_id):
        obj, _ = cls.objects.get_or_create(chat_id=chat_id)
        return obj

    def set(self, state, **kwargs):
        self.state = state
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.save()

    def reset(self):
        self.state = ''
        self.pending_token = ''
        self.pending_bot_pk = None
        self.save()


class RecurringBusyTime(models.Model):
    """Повторяющийся занятый интервал времени пользователя (напр. обед, статус-митинг)."""
    REPEAT_DAILY = 'daily'
    REPEAT_WEEKDAYS = 'weekdays'
    REPEAT_CHOICES = [
        ('daily', 'Ежедневно'),
        ('weekdays', 'По будням (пн–пт)'),
    ]

    owner = models.ForeignKey(
        SiteUser, on_delete=models.CASCADE, related_name='recurring_busy_times',
        verbose_name='Владелец',
    )
    title = models.CharField('Название', max_length=100)
    start_time = models.TimeField('Начало')
    end_time = models.TimeField('Конец')
    repeat = models.CharField('Повтор', max_length=20, choices=REPEAT_CHOICES, default=REPEAT_DAILY)
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Повторяющееся занятое время'
        verbose_name_plural = 'Повторяющееся занятое время'
        ordering = ['start_time']

    def __str__(self):
        return f'{self.title} {self.start_time:%H:%M}–{self.end_time:%H:%M} ({self.get_repeat_display()})'


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


class ExcelSession(models.Model):
    """Сессия Excel Studio — хранит таблицу и конфиги столбцов."""
    id = models.UUIDField(primary_key=True, default=_uuid.uuid4, editable=False)
    user = models.ForeignKey(
        SiteUser, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='excel_sessions',
    )
    space = models.ForeignKey(
        Space, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='excel_sessions',
    )
    filename = models.CharField(max_length=256, default='export')
    columns = models.JSONField(default=list)   # [str, ...]
    rows = models.JSONField(default=list)       # [{col: val}, ...]
    # {colName: {prompt, key_col, status, source, wiki_tpl_slug}}
    col_configs = models.JSONField(default=dict)
    tg_chat_id = models.BigIntegerField(null=True, blank=True)
    created_via = models.CharField(max_length=32, default='web')  # 'web'|'telegram'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Excel-сессия'
        verbose_name_plural = 'Excel-сессии'
        ordering = ['-created_at']


class MicroPreset(models.Model):
    """Микропресет Excel Studio — сохранённые конфиги столбцов для повторного использования."""
    user = models.ForeignKey(SiteUser, on_delete=models.CASCADE, related_name='micropresets')
    name = models.CharField(max_length=200)
    wiki_slug = models.CharField(max_length=300, blank=True)  # контекстная вики-страница
    col_configs = models.JSONField(default=dict)  # {colName: {prompt, key_col, ...}}
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Микропресет'
        verbose_name_plural = 'Микропресеты'
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} ({self.user})'


class DirectMessage(models.Model):
    """Личное сообщение между участниками пространства."""
    sender = models.ForeignKey(SiteUser, on_delete=models.CASCADE, related_name='sent_dms')
    recipient = models.ForeignKey(SiteUser, on_delete=models.CASCADE, related_name='received_dms')
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='dms', null=True, blank=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Личное сообщение'
        verbose_name_plural = 'Личные сообщения'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.sender} → {self.recipient}: {self.text[:40]}'


class BPChatTopic(models.Model):
    """Топик группы BP в Telegram."""
    thread_id = models.IntegerField('Thread ID', unique=True)
    name = models.CharField('Название', max_length=200)
    icon_color = models.IntegerField('Цвет иконки', default=0)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    last_message_at = models.DateTimeField('Последнее сообщение', null=True, blank=True)

    class Meta:
        verbose_name = 'Топик БП'
        verbose_name_plural = 'Топики БП'
        ordering = ['-last_message_at']

    def __str__(self):
        return self.name


class BPChatMessage(models.Model):
    """Сообщение из группы BP."""
    topic = models.ForeignKey(BPChatTopic, on_delete=models.CASCADE, related_name='bp_messages')
    tg_message_id = models.BigIntegerField('TG message_id', unique=True)
    from_name = models.CharField('Имя отправителя', max_length=200)
    from_tg_id = models.BigIntegerField('TG user_id', null=True, blank=True)
    from_username = models.CharField('TG username', max_length=100, blank=True)
    from_site_user = models.ForeignKey(
        SiteUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bp_messages', verbose_name='Пользователь сайта',
    )
    text = models.TextField('Текст')
    created_at = models.DateTimeField('Отправлено', auto_now_add=True)
    tg_date = models.DateTimeField('Дата TG', null=True, blank=True)
    via_site = models.BooleanField('Отправлено с сайта', default=False)

    class Meta:
        verbose_name = 'Сообщение БП'
        verbose_name_plural = 'Сообщения БП'
        ordering = ['tg_date', 'created_at']

    def __str__(self):
        return f'{self.from_name}: {self.text[:40]}'
