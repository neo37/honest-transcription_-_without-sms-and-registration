import os
from pathlib import Path
import environ

env = environ.Env(DEBUG=(bool, False))
BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

# За прокси (nginx): доверять X-Forwarded-Proto и разрешённые origins для CSRF
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

INSTALLED_APPS = [
    'recordings',   # до admin — чтобы наши шаблоны admin/ переопределяли стандартные
    'wiki_kb',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'meetrec.urls'
WSGI_APPLICATION = 'meetrec.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'recordings.context_processors.site_user',
            ],
        },
    },
]

DATABASES = {
    'default': env.db(default='sqlite:///' + str(BASE_DIR / 'db.sqlite3')),
}

MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))
RECORDINGS_DOWNLOAD_DIR = MEDIA_ROOT / 'recordings'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = env('TIME_ZONE', default='Europe/Moscow')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'recordings' / 'static'] if (BASE_DIR / 'recordings' / 'static').exists() else []
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Лимиты загрузки: OCR (PDF/изображения) и загрузки видео
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int('DATA_UPLOAD_MAX_MEMORY_SIZE', default=300 * 1024 * 1024)  # 300 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = env.int('FILE_UPLOAD_MAX_MEMORY_SIZE', default=300 * 1024 * 1024)  # 300 MB

# Session: ensure cookie is saved and sent after login (behind proxy HTTPS set SESSION_COOKIE_SECURE=True in env)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=False)

# S3 (FirstVDS)
S3_ENDPOINT_URL = env('S3_ENDPOINT_URL', default='https://s3.firstvds.ru')
S3_ACCESS_KEY = env('S3_ACCESS_KEY', default='')
S3_SECRET_KEY = env('S3_SECRET_KEY', default='')
S3_BUCKET = env('S3_BUCKET', default='meet')
S3_PREFIX = env('S3_PREFIX', default='')
S3_USE_SSL = env.bool('S3_USE_SSL', default=True)
S3_VERIFY_SSL = env.bool('S3_VERIFY_SSL', default=True)

# Fixed login for site (cached in session) — legacy, kept for reference
SITE_LOGIN_USERNAME = env('SITE_LOGIN_USERNAME', default='adminbp')
SITE_LOGIN_PASSWORD = env('SITE_LOGIN_PASSWORD', default='')

# BP-пространство: участники видят все записи без лимита
BP_SPACE_SLUG = 'org-bp'

# Временный пароль для первого входа (общий для всех новых пользователей)
FIRST_LOGIN_PASSWORD = env('FIRST_LOGIN_PASSWORD', default='')
BP_EMAILS = [
    'k.goncharov@core.business-pad.com',
    'e.leonov@core.business-pad.com',
    'a.timofeev@core.business-pad.com',
    'v.kiselev@core.business-pad.com',
    'd.petrov@core.business-pad.com',
    'i.smirnov@core.business-pad.com',
    'n.ivanova@core.business-pad.com',
    'm.sokolov@core.business-pad.com',
    'o.popova@core.business-pad.com',
    'p.novikov@core.business-pad.com',
    'a.volkov@core.business-pad.com',
    's.morozov@core.business-pad.com',
    'v.fedorov@core.business-pad.com',
    'y.mikhailov@core.business-pad.com',
    't.alexeev@core.business-pad.com',
    'r.lebedev@core.business-pad.com',
    'i.semyonov@core.business-pad.com',
    'e.egorov@core.business-pad.com',
    'a.pavlov@core.business-pad.com',
    'n.kozlov@core.business-pad.com',
    's.stepanov@core.business-pad.com',
    'v.nikolaev@core.business-pad.com',
    'o.orlov@core.business-pad.com',
    'a.andreev@core.business-pad.com',
    'm.makarov@core.business-pad.com',
    'i.nikitin@core.business-pad.com',
    'n.zakharov@core.business-pad.com',
    'v.zaytsev@core.business-pad.com',
    'p.solovyov@core.business-pad.com',
    'e.popov@core.business-pad.com',
    'a.krylova@core.business-pad.com',
    'v.vorobev@core.business-pad.com',
    'i.kuznetsov@core.business-pad.com',
    'd.vinogradov@core.business-pad.com',
    'n.kozlova@core.business-pad.com',
    's.nikiforova@core.business-pad.com',
    'v.melnikov@core.business-pad.com',
    'a.morozova@core.business-pad.com',
    'o.volkov@core.business-pad.com',
    'n.korolev@core.business-pad.com',
    'a.gusev@core.business-pad.com',
    'corp-bp@googlegroups.com',
]

# SMTP Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='mail.core.business-pad.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=465)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=True)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=False)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='')

# Telegram Bot
TELEGRAM_BOT_TOKEN = env('TELEGRAM_BOT_TOKEN', default='')

# DaData
DADATA_TOKEN = env('DADATA_TOKEN', default='')

# Master API key (для создания организаций через API)
MASTER_API_KEY = env('MASTER_API_KEY', default='')
TELEGRAM_BOT_USERNAME = env('TELEGRAM_BOT_USERNAME', default='')
SITE_URL = env('SITE_URL', default='')

# LiveKit (ВКС с голосовым агентом)
LIVEKIT_URL = env('LIVEKIT_URL', default='')
LIVEKIT_API_KEY = env('LIVEKIT_API_KEY', default='')
LIVEKIT_API_SECRET = env('LIVEKIT_API_SECRET', default='')

import hashlib as _hashlib
TELEGRAM_WEBHOOK_SECRET = _hashlib.sha256(SECRET_KEY.encode()).hexdigest()[:32]

# BP Group Chat Bot
BP_CHAT_BOT_TOKEN = env('BP_CHAT_BOT_TOKEN', default='')
BP_CHAT_GROUP_ID  = env.int('BP_CHAT_GROUP_ID', default=0)
BP_CHAT_WEBHOOK_SECRET = _hashlib.sha256(f'bp_chat_{SECRET_KEY}'.encode()).hexdigest()[:24]

# Poll interval seconds
POLL_INTERVAL_SECONDS = env.int('POLL_INTERVAL_SECONDS', default=300)  # 5 min
STABLE_SIZE_SECONDS = env.int('STABLE_SIZE_SECONDS', default=300)  # 5 min unchanged = ready to transcribe

# Email для кнопки «Передать в поддержку»
SUPPORT_EMAIL = env('SUPPORT_EMAIL', default='support@example.com')
# Email для кнопки «Поделиться ошибкой» (открывается почтовый клиент)
SHARE_ERROR_EMAIL = env('SHARE_ERROR_EMAIL', default='hexneo36@gmail.com')

# OCR: внешний API. POST файл (multipart), ожидается JSON { "markdown": "..." } или { "text": "..." }, или plain text
OCR_API_URL = env('OCR_API_URL', default='')
OCR_API_KEY = env('OCR_API_KEY', default='')  # опционально: заголовок Authorization: Bearer <key>
OCR_API_TIMEOUT = env.int('OCR_API_TIMEOUT', default=300)
# Публичный API OCR: X-Api-Key заголовок для /api/ocr/submit/ и /api/ocr/status/
OCR_PUBLIC_API_KEY = env('OCR_PUBLIC_API_KEY', default='')

# WhisperX + pyannote: speaker diarization (нужен HuggingFace token)
# Получить бесплатно: huggingface.co → Settings → Access Tokens
# Принять лицензии: huggingface.co/pyannote/speaker-diarization-3.1
#                   huggingface.co/pyannote/segmentation-3.0
HUGGINGFACE_TOKEN = env('HUGGINGFACE_TOKEN', default='')

LLM_URL = os.environ.get('LLM_URL', 'https://r-ai.business-pad.com/api/ai_request/')
LLM_AUTH = os.environ.get('LLM_AUTH', '')
LLM_REFERER = os.environ.get('LLM_REFERER', 'https://core.business-pad.com/')
LLM_MODEL = os.environ.get('LLM_MODEL', 'gpt-4.1-mini')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[{levelname}] {asctime} {name}: {message}',
            'style': '{',
            'datefmt': '%H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'recordings': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'wiki_kb': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Local folder for downloading MP3 before transcription (MEDIA_ROOT set above)

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')

# Supabase Chat
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
