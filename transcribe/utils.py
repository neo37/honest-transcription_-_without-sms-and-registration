"""
Утилиты для приложения транскрибации
"""
import os
import hashlib
import logging
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from .models import Transcription

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Получить IP адрес клиента"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
    return ip


def check_transcription_access(transcription, request, require_password=True):
    """
    Проверяет доступ к транскрипции
    
    Args:
        transcription: Объект Transcription
        request: HTTP запрос
        require_password: Требовать ли пароль если он установлен
    
    Returns:
        tuple: (has_access: bool, is_public_access: bool, error_message: str)
    """
    active_password_phrase = request.session.get('password_phrase', None)
    
    # Если пароль не установлен, доступ открыт
    if not transcription.password_phrase_hash:
        return True, False, None
    
    # Если пароль установлен
    if require_password:
        if not active_password_phrase:
            return False, False, "Доступ запрещен. Необходимо войти по фразе-паролю."
        
        if not transcription.check_password_phrase(active_password_phrase):
            return False, False, "Доступ запрещен. Неверная фраза-пароль."
    
    return True, False, None


def check_public_token_access(transcription, request):
    """
    Проверяет доступ по публичному токену с паролем
    
    Returns:
        tuple: (has_access: bool, is_public_access: bool, error_message: str)
    """
    password_token = request.GET.get('p', None)
    
    if not transcription.password_phrase_hash:
        return False, False, "Публичный доступ к этой транскрипции недоступен"
    
    if not password_token:
        return False, False, "Для доступа к этой транскрипции нужна специальная ссылка с паролем"
    
    # Проверяем токен пароля
    expected_token = hashlib.sha256(
        f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()
    ).hexdigest()[:16]
    
    if password_token != expected_token:
        return False, False, "Неверная ссылка доступа"
    
    # Автоматически входим по паролю для этой сессии
    request.session['password_phrase'] = 'public_access'
    return True, True, None


def generate_password_token(transcription):
    """Генерирует токен для публичной ссылки с паролем"""
    if not transcription.password_phrase_hash or not transcription.public_token:
        return None
    return hashlib.sha256(
        f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()
    ).hexdigest()[:16]


def validate_file_size(file_size, max_size=500 * 1024 * 1024):
    """Валидация размера файла"""
    if file_size > max_size:
        return False, f"Файл слишком большой. Максимальный размер: {max_size / (1024*1024):.0f} МБ"
    if file_size == 0:
        return False, "Файл пустой"
    return True, None


def validate_whisper_model(model_name):
    """Валидация имени модели Whisper"""
    valid_models = [choice[0] for choice in Transcription.WHISPER_MODELS]
    if model_name not in valid_models:
        return 'base', False
    return model_name, True


def get_relative_media_path(full_path):
    """Получить относительный путь от MEDIA_ROOT"""
    if not full_path:
        return None
    
    media_root = str(settings.MEDIA_ROOT)
    if full_path.startswith(media_root):
        return os.path.relpath(full_path, media_root)
    
    # Если путь уже относительный или другой формат
    return full_path.replace(media_root + '/', '').replace('/root/media/', '')


def split_text_into_slides(text, max_chars=100):
    """
    Разбивает текст на слайды для комикса
    
    Args:
        text: Текст для разбиения
        max_chars: Максимальное количество символов на слайд
    
    Returns:
        list: Список текстовых частей
    """
    if not text:
        return []
    
    import re
    # Разбиваем по предложениям
    sentences = re.split(r'([.!?]+)', text)
    text_parts = []
    current_text = ""
    
    for i in range(0, len(sentences), 2):
        if i < len(sentences):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
            current_text += sentence.strip() + " "
            
            # Создаем слайд каждые max_chars символов или по предложениям
            if len(current_text) > max_chars or i+2 >= len(sentences):
                if current_text.strip():
                    text_parts.append(current_text.strip())
                current_text = ""
    
    return text_parts if text_parts else [text]

